"""Correspondance adresse MAC → adresse IP, lue dans la table ARP de Windows (`arp -a`)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable

from wifimanager.models import normalize_mac

ARP_TIMEOUT_SECONDS = 10
_LINE = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+((?:[0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2})\s+(\S+)")
_IGNORED_PREFIXES = ("01:00:5e", "33:33", "ff:ff:ff")  # multicast et diffusion : jamais un appareil

ArpReader = Callable[[], str]


def run_arp() -> str:
    try:
        completed = subprocess.run(
            ["arp", "-a"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=ARP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""  # sans table ARP, on affiche simplement les appareils sans adresse IP
    return completed.stdout


def parse_arp(text: str) -> dict[str, str]:
    """Table `mac → ip`. Les entrées dynamiques passent avant les statiques, le multicast est ignoré."""
    found: dict[str, tuple[int, str]] = {}
    for line in text.splitlines():
        match = _LINE.match(line)
        if match is None:
            continue
        ip, raw_mac, kind = match.groups()
        try:
            mac = normalize_mac(raw_mac)
        except ValueError:
            continue
        if mac.startswith(_IGNORED_PREFIXES):
            continue
        rank = 0 if kind.lower().startswith(("dyn", "dynamic")) else 1  # « dynamique » (fr), « dynamic » (en)
        if mac not in found or rank < found[mac][0]:
            found[mac] = (rank, ip)
    return {mac: ip for mac, (_, ip) in found.items()}
