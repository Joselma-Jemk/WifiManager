"""Consommation de données du hotspot.

Le TOTAL est exact : les compteurs de l'adaptateur du hotspot sont lus tels que Windows les tient, sans droits
administrateur. Le détail PAR APPAREIL n'est pas fourni par Windows : il est attribué ainsi :
- un seul appareil connecté pendant l'intervalle : tout lui est attribué (exact) ;
- plusieurs appareils connectés : le volume est réparti à parts égales (estimation, signalée comme telle) ;
- aucun appareil lisible : le volume reste « non attribué ».

Ce qui est consommé pendant que le service est arrêté n'est pas compté : la première lecture sert de point de départ.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from wifimanager.models import normalize_mac
from wifimanager.ps import PowerShellError, Runner, run_json, run_powershell

_READ_SCRIPT = r"""
$found = [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | Where-Object {
    $_.OperationalStatus -eq 'Up' -and $_.Description -like '*Wi-Fi Direct Virtual*' } | ForEach-Object {
    $address = $_.GetIPProperties().UnicastAddresses | Where-Object {
        $_.Address.AddressFamily -eq 'InterNetwork' -and -not $_.Address.IPAddressToString.StartsWith('169.254') } | Select-Object -First 1
    if ($address) { [pscustomobject]@{ Nic = $_; Ip = $address.Address.IPAddressToString } }
} | Select-Object -First 1
if ($null -eq $found) { @{ found = $false } | ConvertTo-Json -Compress; return }
$stats = $found.Nic.GetIPv4Statistics()
@{ found = $true; ip = $found.Ip; received = [int64]$stats.BytesReceived; sent = [int64]$stats.BytesSent } | ConvertTo-Json -Compress
"""


@dataclass(frozen=True)
class Counters:
    """Compteurs de l'adaptateur du hotspot. `received` = envoyé par les appareils, `sent` = reçu par les appareils."""

    ip: str  # adresse du PC sur le réseau du hotspot : la passerelle des appareils
    received: int
    sent: int


def read_counters(runner: Runner = run_powershell) -> Counters | None:
    """Compteurs actuels, ou None si le hotspot n'a pas d'adaptateur actif."""
    try:
        data = run_json(_READ_SCRIPT, runner)
    except PowerShellError:
        return None
    if not data.get("found"):
        return None
    return Counters(ip=str(data["ip"]), received=int(data["received"]), sent=int(data["sent"]))


@dataclass
class DeviceUsage:
    uploaded: int = 0  # octets envoyés par l'appareil vers Internet
    downloaded: int = 0  # octets reçus par l'appareil
    estimated: bool = False  # vrai si une part vient d'une répartition entre plusieurs appareils

    @property
    def total(self) -> int:
        return self.uploaded + self.downloaded


@dataclass
class UsageTracker:
    since: float = field(default_factory=time.time)
    uploaded: int = 0
    downloaded: int = 0
    unattributed: int = 0
    devices: dict[str, DeviceUsage] = field(default_factory=dict)
    _last: tuple[int, int] | None = None

    @property
    def total(self) -> int:
        return self.uploaded + self.downloaded

    def record(self, counters: Counters, connected: list[str]) -> None:
        """Compte ce qui s'est passé depuis la lecture précédente et l'attribue aux appareils connectés."""
        last, self._last = self._last, (counters.received, counters.sent)
        if last is None:
            return  # première lecture : simple point de départ
        # Un compteur qui recule signifie que l'adaptateur a redémarré : ce qu'il affiche est ce qui a passé depuis.
        up = counters.received - last[0] if counters.received >= last[0] else counters.received
        down = counters.sent - last[1] if counters.sent >= last[1] else counters.sent
        if up == 0 and down == 0:
            return
        self.uploaded += up
        self.downloaded += down

        macs = sorted({normalize_mac(mac) for mac in connected})
        if not macs:
            self.unattributed += up + down
            return
        count = len(macs)
        for index, mac in enumerate(macs):
            usage = self.devices.setdefault(mac, DeviceUsage())
            # Répartition entière : le reste de la division va aux premiers appareils, rien ne se perd.
            usage.uploaded += up // count + (1 if index < up % count else 0)
            usage.downloaded += down // count + (1 if index < down % count else 0)
            if count > 1:
                usage.estimated = True

    def reset(self) -> None:
        self.since = time.time()
        self.uploaded = self.downloaded = self.unattributed = 0
        self.devices.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "since": self.since,
            "uploaded": self.uploaded,
            "downloaded": self.downloaded,
            "unattributed": self.unattributed,
            "devices": {
                mac: {"uploaded": d.uploaded, "downloaded": d.downloaded, "estimated": d.estimated}
                for mac, d in self.devices.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Any) -> UsageTracker:
        tracker = cls()
        if not isinstance(data, dict):
            return tracker

        def number(value: Any) -> int:
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

        since = data.get("since")
        tracker.since = float(since) if isinstance(since, (int, float)) else tracker.since
        tracker.uploaded = number(data.get("uploaded"))
        tracker.downloaded = number(data.get("downloaded"))
        tracker.unattributed = number(data.get("unattributed"))
        devices = data.get("devices")
        for mac, raw in devices.items() if isinstance(devices, dict) else []:
            try:
                tracker.devices[normalize_mac(str(mac))] = DeviceUsage(
                    number(raw.get("uploaded")), number(raw.get("downloaded")), raw.get("estimated") is True
                )
            except (ValueError, AttributeError):
                continue
        return tracker


def format_bytes(count: int) -> str:
    """Taille lisible : « 1,4 Go », « 530 Mo », « 12 Ko »."""
    value = float(count)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if value < 1000 or unit == "To":
            text = f"{value:.0f}" if unit in ("o", "Ko") or value >= 100 else f"{value:.1f}".replace(".", ",")
            return f"{text} {unit}"
        value /= 1000
    return f"{count} o"
