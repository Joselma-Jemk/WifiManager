"""Surveillance : relance après une extinction involontaire, jamais après un arrêt voulu."""

from wifimanager.models import HotspotState
from wifimanager.watchdog import Action, Watchdog, WatchdogConfig

ON = HotspotState(available=True, state="On", ssid="X")
OFF = HotspotState(available=True, state="Off", ssid="X")
TRANSITION = HotspotState(available=True, state="InTransition", ssid="X")
UNAVAILABLE = HotspotState(available=False, reason="Pas d'Internet")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(enabled: bool = True, delay: float = 10, attempts: int = 3, cooldown: float = 300):
    clock = Clock()
    config = WatchdogConfig(enabled=enabled, delay_seconds=delay, max_attempts=attempts, cooldown_seconds=cooldown)
    return Watchdog(config, clock), clock


def test_restarts_after_delay_when_it_turns_off_by_itself() -> None:
    dog, clock = make()
    assert dog.observe(ON) is Action.NONE
    assert dog.observe(OFF) is Action.NONE  # il vient de s'éteindre : on attend, c'est peut-être passager
    clock.now += 5
    assert dog.observe(OFF) is Action.NONE
    clock.now += 6
    assert dog.observe(OFF) is Action.RESTART


def test_disabled_watchdog_never_restarts() -> None:
    dog, clock = make(enabled=False)
    dog.observe(ON)
    clock.now += 100
    assert dog.observe(OFF) is Action.NONE


def test_never_restarts_a_hotspot_it_never_saw_on() -> None:
    dog, clock = make()
    assert dog.observe(OFF) is Action.NONE
    clock.now += 100
    assert dog.observe(OFF) is Action.NONE


def test_user_stop_is_never_undone() -> None:
    dog, clock = make()
    dog.observe(ON)
    dog.user_stopped()
    clock.now += 100
    assert dog.observe(OFF) is Action.NONE


def test_user_start_makes_the_hotspot_wanted() -> None:
    dog, clock = make(delay=0)
    dog.user_started()
    assert dog.observe(OFF) is Action.RESTART


def test_transition_and_unavailable_do_nothing() -> None:
    dog, clock = make(delay=0)
    dog.observe(ON)
    assert dog.observe(TRANSITION) is Action.NONE
    assert dog.observe(UNAVAILABLE) is Action.NONE


def test_gives_up_once_after_max_attempts_then_waits_for_cooldown() -> None:
    dog, clock = make(delay=0, attempts=2, cooldown=300)
    dog.observe(ON)
    assert dog.observe(OFF) is Action.RESTART
    assert dog.observe(OFF) is Action.RESTART
    assert dog.observe(OFF) is Action.GIVE_UP  # prévient l'utilisateur, une seule fois
    assert dog.observe(OFF) is Action.NONE
    clock.now += 301
    assert dog.observe(OFF) is Action.RESTART  # nouvelle série après le délai de repos


def test_success_resets_the_attempt_counter() -> None:
    dog, clock = make(delay=0, attempts=2)
    dog.observe(ON)
    assert dog.observe(OFF) is Action.RESTART
    assert dog.attempts == 1
    dog.observe(ON)  # la relance a marché
    assert dog.attempts == 0
