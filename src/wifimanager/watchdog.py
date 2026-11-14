"""Surveillance du hotspot : le relance s'il s'éteint tout seul, sans jamais contrarier un arrêt voulu.

Logique pure (sans Qt ni PowerShell), donc testable avec une horloge factice.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from wifimanager.models import HotspotState


class Action(Enum):
    NONE = "rien"
    RESTART = "relancer"
    GIVE_UP = "abandonner"  # émis une seule fois par série d'échecs, pour prévenir l'utilisateur


@dataclass
class WatchdogConfig:
    enabled: bool = False  # désactivé par défaut : on ne relance que si l'utilisateur l'a demandé
    delay_seconds: float = 10.0  # attente avant de considérer que l'extinction n'est pas passagère
    max_attempts: int = 3
    cooldown_seconds: float = 300.0  # après trop d'échecs, nouvelle série d'essais au bout de ce délai


class Watchdog:
    def __init__(self, config: WatchdogConfig, clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self._clock = clock
        self._desired_on = False  # le hotspot doit-il être allumé ? Vrai dès qu'on le voit allumé ou qu'on le démarre
        self._off_since: float | None = None
        self._attempts = 0
        self._last_attempt = 0.0
        self._gave_up = False

    @property
    def attempts(self) -> int:
        return self._attempts

    def user_started(self) -> None:
        self._desired_on = True
        self._reset_failures()

    def user_stopped(self) -> None:
        """Arrêt demandé par l'utilisateur dans l'application : on ne relance jamais ce hotspot-là."""
        self._desired_on = False
        self._reset_failures()

    def _reset_failures(self) -> None:
        self._off_since = None
        self._attempts = 0
        self._gave_up = False

    def observe(self, state: HotspotState) -> Action:
        """À appeler après chaque lecture de l'état. Dit s'il faut relancer le hotspot."""
        if not state.available:
            return Action.NONE  # impossible d'agir sans connexion Internet à partager
        if state.is_on:
            self._desired_on = True
            self._reset_failures()
            return Action.NONE
        if state.state != "Off" or not self._desired_on or not self.config.enabled:
            return Action.NONE  # en transition, jamais allumé, ou surveillance désactivée

        now = self._clock()
        if self._off_since is None:
            self._off_since = now
        if now - self._off_since < self.config.delay_seconds:
            return Action.NONE

        if self._attempts >= self.config.max_attempts:
            if now - self._last_attempt >= self.config.cooldown_seconds:
                self._attempts = 0
                self._gave_up = False
            elif not self._gave_up:
                self._gave_up = True
                return Action.GIVE_UP
            else:
                return Action.NONE

        self._attempts += 1
        self._last_attempt = now
        self._off_since = now  # laisse à Windows le temps de redémarrer avant un nouvel essai
        return Action.RESTART
