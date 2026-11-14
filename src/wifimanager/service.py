"""Service WifiManager (administrateur) : applique les règles d'accès, pose les blocages, compte les données.

La fenêtre tourne sans droits ; ce service garde seul le droit de toucher au pare-feu. Il tourne tant que la session
Windows est ouverte, de sorte que les échéances (6 h, 12 h, 24 h) tiennent même si la fenêtre est fermée.

Choix de sûreté :
- si la lecture du hotspot échoue, les blocages en place sont conservés (on ne débloque jamais sur une erreur) ;
- les blocages vivent dans une session dynamique : si le service s'arrête, ils disparaissent, jamais de blocage orphelin ;
- l'adresse du PC sur le réseau du hotspot (la passerelle des appareils) n'est jamais bloquée ;
- le canal local n'accepte que l'utilisateur Windows qui a lancé le service.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from wifimanager import hotspot
from wifimanager.models import Client, HotspotState, normalize_mac
from wifimanager.neighbors import ArpReader, run_arp
from wifimanager.policy import Policy, PolicyError
from wifimanager.ps import PowerShellError, Runner
from wifimanager.settings import data_dir
from wifimanager.usage import Counters, UsageTracker, read_counters
from wifimanager.wfp import Firewall, WfpError

TICK_SECONDS = 5
MAX_MESSAGE_BYTES = 16 * 1024
SERVICE_VERSION = 1


def state_file() -> Path:
    return data_dir() / "service-state.json"


def pipe_name() -> str:
    """Nom du canal local : propre à l'utilisateur Windows courant."""
    return f"wifimanager-{getpass.getuser()}"


class ServiceCore:
    """Toute la logique du service, sans Qt : testable avec un faux pare-feu et un faux hotspot."""

    def __init__(
        self,
        runner: Runner,
        arp_reader: ArpReader,
        firewall: Firewall,
        policy: Policy,
        usage: UsageTracker,
        path: Path | None = None,
        counters_reader: Callable[[], Counters | None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._runner = runner
        self._arp_reader = arp_reader
        self._firewall = firewall
        self.policy = policy
        self.usage = usage
        self._path = path or state_file()
        self._counters_reader = counters_reader or (lambda: read_counters(runner))
        self._clock = clock
        self._state: HotspotState | None = None
        self._names: dict[str, tuple[str, ...]] = {}  # derniers noms d'hôte connus, même pour un appareil parti
        self._last_ips: dict[str, str] = {}
        self._events: list[dict[str, Any]] = []
        self._event_seq = 0
        self.last_error: str | None = None
        self.last_tick: float | None = None
        self._saved = ""
        self.on_shutdown: Callable[[], None] | None = None  # branché par main() : demande l'arrêt du service

    # --- sauvegarde ----------------------------------------------------------------------

    @classmethod
    def load(
        cls,
        runner: Runner,
        arp_reader: ArpReader,
        firewall: Firewall,
        path: Path | None = None,
        **kwargs: Any,
    ) -> ServiceCore:
        target = path or state_file()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        return cls(
            runner,
            arp_reader,
            firewall,
            Policy.from_dict(raw.get("policy")),
            UsageTracker.from_dict(raw.get("usage")),
            target,
            **kwargs,
        )

    def save(self) -> None:
        payload = json.dumps({"policy": self.policy.to_dict(), "usage": self.usage.to_dict()}, ensure_ascii=False)
        if payload == self._saved:
            return  # rien n'a changé : on ne réécrit pas le disque toutes les 5 secondes
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)
        self._saved = payload

    # --- cycle de surveillance -------------------------------------------------------------

    def tick(self) -> None:
        """Lit le hotspot, met à jour les règles et les compteurs, puis aligne les blocages."""
        self.last_tick = self._clock()
        try:
            state = hotspot.read_state(self._runner, self._arp_reader)
        except PowerShellError as exc:
            self.last_error = f"Lecture du hotspot impossible : {exc}"
            return  # on ne touche à rien : une erreur de lecture ne doit jamais débloquer qui que ce soit
        self._state = state
        clients = state.clients if state.available else ()
        for client in clients:
            self._names[client.mac] = client.names
            if client.ip:
                self._last_ips[client.mac] = client.ip

        for event in self.policy.observe([client.mac for client in clients]):
            self._event_seq += 1  # numéro croissant : la fenêtre sait quels événements elle n'a pas encore annoncés
            self._events.append({"seq": self._event_seq, "kind": event.kind, "mac": event.mac or "", "detail": event.detail})
        self._events = self._events[-50:]

        counters = self._counters_reader()
        if counters is not None:
            self._firewall.set_protected({counters.ip})
            self.usage.record(counters, [client.mac for client in clients])

        self._enforce(clients)
        self.save()

    def _enforce(self, clients: tuple[Client, ...] | list[Client]) -> None:
        """Bloque l'adresse de chaque appareil connecté dont la règle l'exige, et libère les autres."""
        wanted = {c.ip for c in clients if c.ip and self.policy.is_blocked(c.mac)}
        try:
            self._firewall.sync(wanted)
        except WfpError as exc:
            self.last_error = f"Pare-feu : {exc}"
        else:
            self.last_error = None

    def _apply_now(self) -> None:
        """Applique tout de suite un ordre de l'utilisateur, sans attendre la prochaine lecture."""
        self._enforce(self._state.clients if self._state is not None and self._state.available else ())
        self.save()

    # --- demandes de la fenêtre --------------------------------------------------------------

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        try:
            if action == "status":
                return self._status()
            if action in ("block", "allow", "forget"):
                mac = normalize_mac(str(request.get("mac", "")))
                getattr(self.policy, action)(mac)
            elif action == "allow_for":
                self.policy.allow_for(normalize_mac(str(request.get("mac", ""))), request.get("seconds"))
            elif action == "create_pass":
                kwargs = {"claim_seconds": request["claim_seconds"]} if "claim_seconds" in request else {}
                guest = self.policy.create_pass(request.get("seconds"), **kwargs)
                self._apply_now()
                return {"ok": True, "pass": _pass_dict(guest)}
            elif action == "revoke_pass":
                self.policy.revoke_pass(str(request.get("id", "")))
            elif action == "set_default_mode":
                self.policy.set_default_mode(str(request.get("mode", "")))
            elif action == "reset_usage":
                self.usage.reset()
            elif action == "shutdown":
                if self.on_shutdown is not None:
                    self.on_shutdown()  # l'arrêt retire tous les blocages : la session du pare-feu se ferme avec lui
                return {"ok": True}
            else:
                return {"ok": False, "error": f"Action inconnue : {action}"}
        except (PolicyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        self._apply_now()
        return {"ok": True}

    def _status(self) -> dict[str, Any]:
        connected = {c.mac: c for c in (self._state.clients if self._state is not None and self._state.available else ())}
        macs = sorted(set(connected) | set(self.policy.rules))
        devices = []
        for mac in macs:
            client = connected.get(mac)
            usage = self.usage.devices.get(mac)
            devices.append(
                {
                    "mac": mac,
                    "connected": client is not None,
                    "ip": client.ip if client else self._last_ips.get(mac),
                    "names": list(client.names if client else self._names.get(mac, ())),
                    **self.policy.status(mac),
                    "blocked": self.policy.is_blocked(mac),
                    "uploaded": usage.uploaded if usage else 0,
                    "downloaded": usage.downloaded if usage else 0,
                    "estimated": usage.estimated if usage else False,
                }
            )
        return {
            "ok": True,
            "service": {
                "version": SERVICE_VERSION,
                "blocking": True,
                "last_tick": self.last_tick,
                "error": self.last_error,
            },
            "default_mode": self.policy.default_mode,
            "devices": devices,
            "passes": [_pass_dict(g) for g in self.policy.pending_passes()],
            "events": self._events[-20:],
            "usage": {
                "since": self.usage.since,
                "uploaded": self.usage.uploaded,
                "downloaded": self.usage.downloaded,
                "unattributed": self.usage.unattributed,
            },
        }

    def close(self) -> None:
        self.save()
        self._firewall.close()


def _pass_dict(guest: Any) -> dict[str, Any]:
    return {
        "id": guest.id,
        "duration": guest.duration,
        "created_at": guest.created_at,
        "claim_deadline": guest.claim_deadline,
    }


# --- exécution : minuterie et canal local (Qt) ---------------------------------------------------------


def main() -> int:
    from PySide6.QtCore import QCoreApplication, QObject, QTimer
    from PySide6.QtNetwork import QLocalServer

    from wifimanager.psession import PowerShellSession
    from wifimanager.wfp import NativeEngine

    app = QCoreApplication(sys.argv)
    session = PowerShellSession()
    try:
        firewall = Firewall(NativeEngine())
    except WfpError as exc:
        print(f"Impossible d'ouvrir le pare-feu : {exc}", file=sys.stderr)
        session.close()
        return 2
    core = ServiceCore.load(session, run_arp, firewall)

    class Server(QObject):
        def __init__(self) -> None:
            super().__init__()
            self._server = QLocalServer(self)
            self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)  # l'utilisateur seul
            self._server.newConnection.connect(self._on_connection)
            QLocalServer.removeServer(pipe_name())
            if not self._server.listen(pipe_name()):
                raise RuntimeError(f"Canal local indisponible : {self._server.errorString()}")

        def _on_connection(self) -> None:
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            data = bytearray()
            while b"\n" not in data and len(data) <= MAX_MESSAGE_BYTES:
                if not socket.waitForReadyRead(3000):
                    break
                data += bytes(socket.readAll())
            try:
                request = json.loads(bytes(data).split(b"\n", 1)[0].decode("utf-8"))
                reply = core.handle(request) if isinstance(request, dict) else {"ok": False, "error": "Requête invalide"}
            except (ValueError, UnicodeDecodeError):
                reply = {"ok": False, "error": "Requête illisible"}
            except Exception as exc:  # noqa: BLE001 - le client doit toujours recevoir une réponse
                reply = {"ok": False, "error": f"Erreur interne : {type(exc).__name__}"}
            socket.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            socket.flush()
            socket.waitForBytesWritten(3000)
            socket.disconnectFromServer()

    try:
        server = Server()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        core.close()
        session.close()
        return 3

    core.on_shutdown = lambda: QTimer.singleShot(300, app.quit)  # laisse partir la réponse avant de s'arrêter
    timer = QTimer()
    timer.timeout.connect(core.tick)
    timer.start(TICK_SECONDS * 1000)
    QTimer.singleShot(0, core.tick)

    def shutdown() -> None:
        core.close()
        session.close()

    app.aboutToQuit.connect(shutdown)
    code = app.exec()
    del server
    return code


if __name__ == "__main__":
    raise SystemExit(main())
