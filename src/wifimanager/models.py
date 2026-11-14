"""Modèles de données : ce que l'application sait du hotspot, sans jamais le mot de passe."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_MAC_SEPARATORS = re.compile(r"[-:.]")
_HEX_MAC = re.compile(r"^[0-9a-f]{12}$")


def normalize_mac(mac: str) -> str:
    """Adresse MAC en minuscules, séparée par « : ». Lève ValueError si elle n'est pas valide."""
    digits = _MAC_SEPARATORS.sub("", mac).lower()
    if not _HEX_MAC.match(digits):
        raise ValueError(f"Adresse MAC invalide : {mac!r}")
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


@dataclass(frozen=True)
class Client:
    """Appareil connecté au hotspot."""

    mac: str
    names: tuple[str, ...] = ()
    ip: str | None = None

    @property
    def label(self) -> str:
        """Nom à afficher : le premier nom d'hôte connu, sinon l'adresse MAC."""
        return self.names[0] if self.names else self.mac


@dataclass(frozen=True)
class HotspotState:
    """État du hotspot à un instant donné."""

    available: bool
    reason: str = ""
    state: str = "Unknown"  # On, Off, InTransition, Unknown
    ssid: str = ""
    band: str = ""
    client_count: int = 0
    max_clients: int = 0
    clients: tuple[Client, ...] = field(default_factory=tuple)

    @property
    def is_on(self) -> bool:
        return self.available and self.state == "On"
