"""Règles d'accès : qui a le droit d'utiliser le hotspot, et jusqu'à quand. Logique pure, sans Windows ni Qt.

Un appareil a une règle parmi :
- allowed   : autorisé sans limite ;
- blocked   : bloqué (par l'utilisateur, ou parce qu'un accès temporaire a expiré) ;
- temporary : autorisé jusqu'à une échéance, puis bloqué automatiquement ;
- pending   : nouvel appareil retenu en attendant la décision de l'utilisateur (mode strict).

Mode « open » : un appareil sans règle est autorisé. Mode « strict » : un nouvel appareil est retenu (bloqué) tant que
l'utilisateur ne l'a pas autorisé, SAUF s'il se présente avec un pass invité valide.

Un pass invité est créé avec une durée (6 h, 12 h, 24 h…) et affiché sous forme de QR code. Le premier NOUVEL appareil
qui se connecte pendant sa fenêtre de validité le réclame : il est alors autorisé pour la durée du pass, puis bloqué.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from wifimanager.models import normalize_mac

ALLOWED = "allowed"
BLOCKED = "blocked"
TEMPORARY = "temporary"
PENDING = "pending"
_MODES = {ALLOWED, BLOCKED, TEMPORARY, PENDING}

OPEN = "open"
STRICT = "strict"

MIN_SECONDS = 60
MAX_SECONDS = 7 * 24 * 3600
DEFAULT_CLAIM_SECONDS = 1800  # un pass non réclamé en 30 minutes s'annule
MAX_CLAIM_SECONDS = 24 * 3600
MAX_PASSES = 20


class PolicyError(Exception):
    """Demande invalide (durée hors limites, mode inconnu, pass introuvable…)."""


@dataclass
class Rule:
    mode: str
    expires_at: float | None = None  # seulement pour « temporary »
    reason: str = ""
    pass_id: str | None = None  # pass invité à l'origine de l'accès temporaire


@dataclass
class GuestPass:
    id: str
    duration: int  # durée de l'accès accordé à l'appareil qui le réclame, en secondes
    created_at: float
    claim_deadline: float  # au-delà, le pass n'est plus réclamable
    claimed_mac: str | None = None


@dataclass(frozen=True)
class Event:
    kind: str  # new_device, new_device_held, pass_claimed, access_expired, pass_expired
    mac: str | None = None
    detail: str = ""


def _check_seconds(seconds: int, minimum: int = MIN_SECONDS, maximum: int = MAX_SECONDS) -> int:
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not minimum <= seconds <= maximum:
        raise PolicyError(f"La durée doit être comprise entre {minimum} s et {maximum // 3600} h")
    return seconds


class Policy:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self.rules: dict[str, Rule] = {}
        self.passes: dict[str, GuestPass] = {}
        self.known: set[str] = set()  # appareils déjà vus : seuls les nouveaux peuvent réclamer un pass
        self.default_mode = OPEN

    # --- ordres de l'utilisateur ----------------------------------------------------------

    def set_default_mode(self, mode: str) -> None:
        if mode not in (OPEN, STRICT):
            raise PolicyError(f"Mode inconnu : {mode!r}")
        if mode == STRICT and self.default_mode != STRICT:
            # Passer en strict ne doit jamais couper les appareils déjà connus : ils sont autorisés d'office.
            for mac in self.known:
                self.rules.setdefault(mac, Rule(ALLOWED, reason="déjà connu au passage en mode strict"))
        self.default_mode = mode

    def block(self, mac: str, reason: str = "bloqué par l'utilisateur") -> None:
        self.rules[normalize_mac(mac)] = Rule(BLOCKED, reason=reason)

    def allow(self, mac: str) -> None:
        self.rules[normalize_mac(mac)] = Rule(ALLOWED)

    def allow_for(self, mac: str, seconds: int) -> None:
        """Accès temporaire : autorisé pendant `seconds`, puis bloqué automatiquement."""
        expires = self._clock() + _check_seconds(seconds)
        self.rules[normalize_mac(mac)] = Rule(TEMPORARY, expires_at=expires)

    def forget(self, mac: str) -> None:
        """Efface la règle : l'appareil retrouve le comportement par défaut."""
        self.rules.pop(normalize_mac(mac), None)

    # --- pass invités ---------------------------------------------------------------------

    def create_pass(self, seconds: int, claim_seconds: int = DEFAULT_CLAIM_SECONDS) -> GuestPass:
        duration = _check_seconds(seconds)
        window = _check_seconds(claim_seconds, maximum=MAX_CLAIM_SECONDS)
        if len(self.passes) >= MAX_PASSES:
            raise PolicyError(f"Trop de pass en attente (maximum {MAX_PASSES})")
        now = self._clock()
        guest = GuestPass(id=secrets.token_hex(4), duration=duration, created_at=now, claim_deadline=now + window)
        self.passes[guest.id] = guest
        return guest

    def revoke_pass(self, pass_id: str) -> None:
        guest = self.passes.pop(pass_id, None)
        if guest is None:
            raise PolicyError("Pass introuvable")
        if guest.claimed_mac is not None:
            rule = self.rules.get(guest.claimed_mac)
            if rule is not None and rule.pass_id == pass_id:
                self.rules[guest.claimed_mac] = Rule(BLOCKED, reason="pass invité révoqué")

    def pending_passes(self) -> list[GuestPass]:
        """Pass encore réclamables, du plus ancien au plus récent."""
        now = self._clock()
        return sorted(
            (g for g in self.passes.values() if g.claimed_mac is None and g.claim_deadline > now),
            key=lambda g: g.created_at,
        )

    # --- observation des appareils connectés ----------------------------------------------------

    def observe(self, connected: list[str]) -> list[Event]:
        """À appeler à chaque lecture du hotspot : fait expirer ce qui doit l'être, et classe les nouveaux appareils."""
        now = self._clock()
        events: list[Event] = []

        for mac, rule in self.rules.items():
            if rule.mode == TEMPORARY and rule.expires_at is not None and rule.expires_at <= now:
                self.rules[mac] = Rule(BLOCKED, reason="accès expiré")
                events.append(Event("access_expired", mac))
        for pass_id, guest in list(self.passes.items()):
            if guest.claimed_mac is None and guest.claim_deadline <= now:
                del self.passes[pass_id]
                events.append(Event("pass_expired", None, pass_id))

        for raw in connected:
            mac = normalize_mac(raw)
            if mac in self.known:
                continue
            self.known.add(mac)
            if mac in self.rules:  # règle déjà posée (par exemple avant sa première apparition) : on la respecte
                continue
            waiting = self.pending_passes()
            if waiting:
                guest = waiting[0]
                guest.claimed_mac = mac
                self.rules[mac] = Rule(TEMPORARY, expires_at=now + guest.duration, pass_id=guest.id)
                events.append(Event("pass_claimed", mac, guest.id))
            elif self.default_mode == STRICT:
                self.rules[mac] = Rule(PENDING, reason="nouvel appareil en attente d'approbation")
                events.append(Event("new_device_held", mac))
            else:
                events.append(Event("new_device", mac))
        return events

    # --- lecture --------------------------------------------------------------------------------

    def is_blocked(self, mac: str) -> bool:
        rule = self.rules.get(normalize_mac(mac))
        if rule is None:
            return False
        if rule.mode == TEMPORARY:
            return rule.expires_at is not None and rule.expires_at <= self._clock()
        return rule.mode in (BLOCKED, PENDING)

    def status(self, mac: str) -> dict[str, Any]:
        """Description d'un appareil pour l'interface : mode, temps restant, raison."""
        key = normalize_mac(mac)
        rule = self.rules.get(key)
        if rule is None:
            return {"mode": ALLOWED, "explicit": False, "remaining": None, "reason": ""}
        remaining = None
        mode = rule.mode
        if rule.mode == TEMPORARY and rule.expires_at is not None:
            remaining = max(0, int(rule.expires_at - self._clock()))
            if remaining == 0:
                mode = BLOCKED
        return {"mode": mode, "explicit": True, "remaining": remaining, "reason": rule.reason}

    # --- sauvegarde -------------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_mode": self.default_mode,
            "known": sorted(self.known),
            "rules": {
                mac: {"mode": r.mode, "expires_at": r.expires_at, "reason": r.reason, "pass_id": r.pass_id}
                for mac, r in self.rules.items()
            },
            "passes": {
                p.id: {
                    "duration": p.duration,
                    "created_at": p.created_at,
                    "claim_deadline": p.claim_deadline,
                    "claimed_mac": p.claimed_mac,
                }
                for p in self.passes.values()
            },
        }

    @classmethod
    def from_dict(cls, data: Any, clock: Callable[[], float] = time.time) -> Policy:
        """Relit un état sauvegardé. Ce qui est abîmé est ignoré : mieux vaut perdre une règle que planter."""
        policy = cls(clock)
        if not isinstance(data, dict):
            return policy
        if data.get("default_mode") in (OPEN, STRICT):
            policy.default_mode = data["default_mode"]
        for mac in data.get("known", []) if isinstance(data.get("known"), list) else []:
            try:
                policy.known.add(normalize_mac(str(mac)))
            except ValueError:
                continue
        rules = data.get("rules")
        for mac, raw in rules.items() if isinstance(rules, dict) else []:
            try:
                key = normalize_mac(str(mac))
                mode = raw["mode"]
                expires = raw.get("expires_at")
                if mode not in _MODES or (expires is not None and not isinstance(expires, (int, float))):
                    continue
                policy.rules[key] = Rule(mode, expires, str(raw.get("reason", "")), raw.get("pass_id"))
                policy.known.add(key)
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        passes = data.get("passes")
        for pass_id, raw in passes.items() if isinstance(passes, dict) else []:
            try:
                claimed = raw.get("claimed_mac")
                policy.passes[str(pass_id)] = GuestPass(
                    id=str(pass_id),
                    duration=_check_seconds(int(raw["duration"])),
                    created_at=float(raw["created_at"]),
                    claim_deadline=float(raw["claim_deadline"]),
                    claimed_mac=normalize_mac(claimed) if claimed else None,
                )
            except (ValueError, KeyError, TypeError, AttributeError, PolicyError):
                continue
        return policy
