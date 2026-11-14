"""« Maintenir allumé » : relance du service de blocage, avec délai et limite d'essais."""

import json

import pytest

from wifimanager import install
from wifimanager.controller import HotspotWorker
from wifimanager.ps import PowerShellError
from wifimanager.service_client import ServiceUnavailable
from wifimanager.watchdog import RestartGate, Watchdog, WatchdogConfig


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# --- RestartGate ---------------------------------------------------------------------------


def test_gate_allows_first_attempt_then_waits_for_the_cooldown() -> None:
    clock = Clock()
    gate = RestartGate(max_attempts=5, cooldown_seconds=20, clock=clock)
    assert gate.allow() is True
    clock.now += 5
    assert gate.allow() is False  # trop tôt
    clock.now += 16
    assert gate.allow() is True


def test_gate_stops_after_max_attempts_in_the_window_then_recovers() -> None:
    clock = Clock()
    gate = RestartGate(max_attempts=3, window_seconds=600, cooldown_seconds=1, clock=clock)
    for _ in range(3):
        assert gate.allow() is True
        clock.now += 2
    assert gate.allow() is False  # trois essais en quelques secondes : on arrête de boucler
    clock.now += 600
    assert gate.allow() is True  # la fenêtre est passée


def test_gate_reset_clears_the_history() -> None:
    clock = Clock()
    gate = RestartGate(max_attempts=1, clock=clock)
    assert gate.allow() is True and gate.allow() is False
    gate.reset()
    assert gate.allow() is True


# --- relance du service par le travailleur ------------------------------------------------------


class Env:
    """Un service qui peut être absent, installé ou non, et un compteur de relances."""

    def __init__(self) -> None:
        self.responding = False
        self.installed = True
        self.starts = 0
        self.start_error: str | None = None
        self.install_checks = 0
        self.clock = Clock()

    def service_call(self, payload: dict) -> dict:
        if not self.responding:
            raise ServiceUnavailable("Le service de blocage n'est pas lancé")
        return {"ok": True, "service": {"error": None}, "devices": [], "passes": [], "events": [], "default_mode": "open",
                "usage": {"since": 0, "uploaded": 0, "downloaded": 0, "unattributed": 0}}

    def is_installed(self) -> bool:
        self.install_checks += 1
        return self.installed

    def start(self) -> None:
        if self.start_error:
            raise PowerShellError(self.start_error)
        self.starts += 1
        self.responding = True  # la tâche relance le service


def hotspot_reply(script: str, env=None) -> str:
    return json.dumps({"available": True, "state": "On", "ssid": "X", "band": "Auto", "client_count": 0, "max_clients": 8, "clients": []})


def make(env: Env, keep: bool = True) -> tuple[HotspotWorker, dict[str, list]]:
    watchdog = Watchdog(WatchdogConfig(enabled=keep))
    worker = HotspotWorker(
        hotspot_reply, lambda: "", watchdog, service_call=env.service_call,
        start_fn=env.start, installed_fn=env.is_installed, service_gate=RestartGate(clock=env.clock),
    )
    seen: dict[str, list] = {"message": [], "failed": []}
    worker.message.connect(seen["message"].append)
    worker.failed.connect(seen["failed"].append)
    return worker, seen


def test_stopped_service_is_restarted_when_keep_alive_is_on(qapp) -> None:
    env = Env()
    worker, seen = make(env)
    worker.poll()
    assert env.starts == 1 and any("relancé" in m for m in seen["message"])


def test_nothing_is_restarted_when_keep_alive_is_off(qapp) -> None:
    env = Env()
    worker, _ = make(env, keep=False)
    worker.poll()
    assert env.starts == 0 and env.install_checks == 0


def test_turning_keep_alive_on_later_starts_supervising(qapp) -> None:
    env = Env()
    worker, _ = make(env, keep=False)
    worker.poll()
    worker.set_auto_restart(True)
    worker.poll()
    assert env.starts == 1


def test_service_that_was_never_installed_is_left_alone(qapp) -> None:
    env = Env()
    env.installed = False
    worker, _ = make(env)
    worker.poll()
    assert env.starts == 0


def test_installed_state_is_not_asked_on_every_poll(qapp) -> None:
    env = Env()
    env.installed = False
    worker, _ = make(env)
    for _ in range(5):
        worker.poll()
    assert env.install_checks == 1  # mémorisé pendant une minute


def test_failed_restart_is_reported_and_retried_only_after_the_cooldown(qapp) -> None:
    env = Env()
    env.start_error = "Accès refusé"
    worker, seen = make(env)
    worker.poll()
    assert "Relance du service de blocage impossible" in seen["failed"][0]
    worker.poll()
    assert len(seen["failed"]) == 1  # délai de 20 s pas écoulé : pas de nouvel essai
    env.clock.now += 25
    worker.poll()
    assert len(seen["failed"]) == 2


def test_service_that_keeps_dying_is_not_restarted_forever(qapp) -> None:
    env = Env()
    worker, _ = make(env)
    for _ in range(10):
        env.responding = False  # il s'arrête de nouveau à chaque fois
        env.clock.now += 25
        worker.poll()
    assert env.starts == 5  # la limite par fenêtre de temps évite une boucle infinie


def test_answering_service_resets_the_attempt_counter(qapp) -> None:
    env = Env()
    worker, _ = make(env)
    worker.poll()  # relancé, répond
    worker.poll()  # répond : le compteur est remis à zéro
    for _ in range(5):
        env.responding = False
        env.clock.now += 25
        worker.poll()
        worker.poll()
    assert env.starts == 6  # 1 + 5 : une longue série de pannes réussies n'épuise pas la limite


# --- installation ---------------------------------------------------------------------------------


def test_install_script_restarts_the_task_if_the_service_crashes() -> None:
    script = install.install_script(r"C:\Python\pythonw.exe", "-m wifimanager.service")
    assert "-RestartCount 5" in script and "-RestartInterval" in script


def test_start_service_reports_success_and_failure() -> None:
    install.start_service(lambda script, env=None: json.dumps({"ok": True}))
    with pytest.raises(PowerShellError, match="introuvable"):
        install.start_service(lambda script, env=None: json.dumps({"ok": False, "error": "Tâche introuvable"}))
