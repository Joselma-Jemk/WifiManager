"""Travailleur Qt : parle à PowerShell dans son propre fil, pour que la fenêtre ne se fige jamais.

Il lit l'état à intervalle régulier, applique les ordres de l'utilisateur (démarrer, arrêter, changer le mot de passe),
et laisse la surveillance relancer le hotspot s'il s'éteint tout seul. Tout ce qu'il apprend sort par des signaux.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from wifimanager import hotspot, install, service_client
from wifimanager.models import HotspotState
from wifimanager.neighbors import ArpReader
from wifimanager.ps import PowerShellError, Runner
from wifimanager.service_client import ServiceUnavailable
from wifimanager.watchdog import Action, RestartGate, Watchdog

SERVICE_START_WAIT_SECONDS = 30
INSTALLED_CACHE_SECONDS = 60


class HotspotWorker(QObject):
    stateChanged = Signal(object)  # HotspotState
    message = Signal(str)  # information pour la barre d'état
    failed = Signal(str)  # erreur à montrer à l'utilisateur
    busyChanged = Signal(bool)
    credentialsApplied = Signal(str, str)  # nom et mot de passe appliqués, pour afficher le QR code
    serviceChanged = Signal(object)  # état du service de blocage (dict), ou None s'il ne répond pas
    passCreated = Signal(object)  # pass invité créé (dict : id, duration, claim_deadline…)

    def __init__(
        self,
        runner: Runner,
        arp_reader: ArpReader,
        watchdog: Watchdog,
        poll_seconds: int = 10,
        parent: QObject | None = None,
        service_call: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        install_fn: Callable[[], None] | None = None,
        uninstall_fn: Callable[[], None] | None = None,
        start_fn: Callable[[], None] | None = None,
        installed_fn: Callable[[], bool] | None = None,
        service_gate: RestartGate | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner
        self._arp_reader = arp_reader
        self._watchdog = watchdog
        self._poll_seconds = poll_seconds
        self._service_call = service_call or service_client.request
        self._install = install_fn or install.install_service
        self._uninstall = uninstall_fn or install.uninstall_service
        # Par défaut on réutilise le PowerShell persistant : en lancer un nouveau coûte environ 7 secondes.
        self._start_service = start_fn or (lambda: install.start_service(runner))
        self._is_installed = installed_fn or (lambda: install.is_installed(runner))
        self._installed_cache: tuple[float, bool] | None = None
        self._service_gate = service_gate or RestartGate()
        self._keep_service = watchdog.config.enabled  # « maintenir allumé » couvre aussi le service de blocage
        self._timer: QTimer | None = None
        self._last_state: HotspotState | None = None

    # --- cycle de vie ---------------------------------------------------------------

    @Slot()
    def start_polling(self) -> None:
        """À appeler depuis le fil du travailleur : le minuteur doit y vivre."""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._timer.start(self._poll_seconds * 1000)
        self.poll()

    @Slot()
    def shutdown(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    # --- lecture et surveillance ----------------------------------------------------------

    @Slot()
    def poll(self) -> None:
        state = self._read()
        self._poll_service()
        if state is None:
            return
        action = self._watchdog.observe(state)
        if action is Action.RESTART:
            self.message.emit(
                f"Le hotspot s'est éteint : relance automatique (essai {self._watchdog.attempts}"
                f"/{self._watchdog.config.max_attempts})"
            )
            self._start_hotspot(automatic=True)
        elif action is Action.GIVE_UP:
            self.failed.emit(
                f"Le hotspot ne repart pas après {self._watchdog.config.max_attempts} essais. "
                "Nouvelle tentative dans quelques minutes, ou relance-le toi-même."
            )

    def _read(self) -> HotspotState | None:
        try:
            state = hotspot.read_state(self._runner, self._arp_reader)
        except PowerShellError as exc:
            self.failed.emit(f"Lecture du hotspot impossible : {exc}")
            return None
        self._last_state = state
        self.stateChanged.emit(state)
        return state

    # --- ordres de l'utilisateur ------------------------------------------------------------

    @Slot()
    def start_hotspot(self) -> None:
        self._watchdog.user_started()
        self._start_hotspot(automatic=False)

    def _start_hotspot(self, automatic: bool) -> None:
        self.busyChanged.emit(True)
        try:
            hotspot.start(self._runner)
        except PowerShellError as exc:
            self.failed.emit(str(exc))
        else:
            if not automatic:
                self.message.emit("Hotspot démarré")
        finally:
            self.busyChanged.emit(False)
        self._read()

    @Slot()
    def stop_hotspot(self) -> None:
        self._watchdog.user_stopped()  # arrêt voulu : la surveillance ne doit jamais le défaire
        self.busyChanged.emit(True)
        try:
            hotspot.stop(self._runner)
        except PowerShellError as exc:
            self.failed.emit(str(exc))
        else:
            self.message.emit("Hotspot arrêté")
        finally:
            self.busyChanged.emit(False)
        self._read()

    @Slot(str, str)
    def change_credentials(self, ssid: str, passphrase: str) -> None:
        """Change le nom et le mot de passe, puis relance le hotspot pour qu'ils s'appliquent."""
        was_on = self._last_state is not None and self._last_state.is_on
        self.busyChanged.emit(True)
        try:
            hotspot.set_credentials(ssid, passphrase, self._runner)
            if was_on:
                self._watchdog.user_stopped()  # redémarrage voulu : pas de relance automatique en parallèle
                hotspot.stop(self._runner)
                hotspot.start(self._runner)
                self._watchdog.user_started()
        except (PowerShellError, ValueError) as exc:
            self.failed.emit(f"Mot de passe non changé : {exc}")
            if was_on:
                self._watchdog.user_started()
        else:
            self.message.emit("Mot de passe changé : les appareils doivent se reconnecter")
            self.credentialsApplied.emit(ssid, passphrase)
        finally:
            self.busyChanged.emit(False)
        self._read()

    # --- service de blocage -----------------------------------------------------------------

    def _poll_service(self) -> None:
        try:
            reply = self._service_call({"action": "status"})
        except ServiceUnavailable:
            self.serviceChanged.emit(None)
            self._revive_service()
            return
        self._service_gate.reset()  # il répond : la prochaine panne repartira d'un compteur propre
        self.serviceChanged.emit(reply if reply.get("ok") else None)

    def _revive_service(self) -> None:
        """Si « maintenir allumé » est actif et que le service installé ne répond plus, le relance (en limitant les essais)."""
        if not self._keep_service or not self._service_installed() or not self._service_gate.allow():
            return
        try:
            self._start_service()
        except PowerShellError as exc:
            self.failed.emit(f"Relance du service de blocage impossible : {exc}")
        else:
            self.message.emit("Le service de blocage s'était arrêté : relancé")

    def _service_installed(self) -> bool:
        """La tâche du service existe-t-elle ? Mémorisé une minute : inutile de le demander à chaque lecture."""
        now = time.monotonic()
        if self._installed_cache is not None and now - self._installed_cache[0] < INSTALLED_CACHE_SECONDS:
            return self._installed_cache[1]
        installed = self._is_installed()
        self._installed_cache = (now, installed)
        return installed

    @Slot(object)
    def service_action(self, payload: dict[str, Any]) -> None:
        """Envoie un ordre au service (bloquer, autoriser, accès temporaire, pass invité…)."""
        self.busyChanged.emit(True)
        reply: dict[str, Any] | None = None
        try:
            reply = self._service_call(payload)
        except ServiceUnavailable as exc:
            self.failed.emit(str(exc))
        finally:
            self.busyChanged.emit(False)
        if reply is not None:
            if not reply.get("ok"):
                self.failed.emit(str(reply.get("error", "Ordre refusé par le service")))
            elif "pass" in reply:
                self.passCreated.emit(reply["pass"])
        self._poll_service()

    @Slot()
    def install_service(self) -> None:
        """Installe le service (une fenêtre UAC), puis attend qu'il réponde."""
        self.busyChanged.emit(True)
        try:
            self._install()
            self._installed_cache = None  # l'état vient de changer
            deadline = time.monotonic() + SERVICE_START_WAIT_SECONDS
            while True:
                try:
                    if self._service_call({"action": "status"}).get("ok"):
                        break
                except ServiceUnavailable:
                    pass
                if time.monotonic() >= deadline:
                    raise PowerShellError("le service ne répond pas après son installation")
                time.sleep(1)
        except PowerShellError as exc:
            self.failed.emit(f"Activation du blocage impossible : {exc}")
        else:
            self.message.emit("Blocage activé : le service démarre à chaque ouverture de session")
        finally:
            self.busyChanged.emit(False)
        self._poll_service()

    @Slot()
    def uninstall_service(self) -> None:
        """Arrête le service (ce qui retire tous les blocages) et supprime sa tâche planifiée."""
        self.busyChanged.emit(True)
        try:
            try:
                self._service_call({"action": "shutdown"})
            except ServiceUnavailable:
                pass  # déjà arrêté
            self._uninstall()
            self._installed_cache = None
        except PowerShellError as exc:
            self.failed.emit(f"Désactivation du blocage impossible : {exc}")
        else:
            self.message.emit("Blocage désactivé : plus aucun appareil n'est bloqué")
        finally:
            self.busyChanged.emit(False)
        self._poll_service()

    @Slot(bool)
    def set_auto_restart(self, enabled: bool) -> None:
        """« Maintenir allumé » : relance le hotspot s'il s'éteint, et le service de blocage s'il s'arrête."""
        self._watchdog.config.enabled = enabled
        self._keep_service = enabled
        self._service_gate.reset()
        self.message.emit("Maintien activé : le hotspot et le blocage seront relancés" if enabled else "Maintien désactivé")
