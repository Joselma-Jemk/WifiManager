"""Hotspot : lecture de l'état, écriture, et protection du mot de passe. Sans Windows grâce à un exécuteur factice."""

import json

import pytest

from wifimanager import hotspot
from wifimanager.models import normalize_mac
from wifimanager.passwords import (
    generate_passphrase,
    validate_passphrase,
    validate_ssid,
)
from wifimanager.ps import PowerShellError


class FakeRunner:
    """Enregistre les scripts et les variables d'environnement reçus, et renvoie une réponse JSON fixée."""

    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.scripts: list[str] = []
        self.envs: list[dict | None] = []

    def __call__(self, script: str, env: dict | None = None) -> str:
        self.scripts.append(script)
        self.envs.append(env)
        return json.dumps(self.reply)


SAMPLE = {
    "available": True,
    "state": "On",
    "ssid": "MON_HOTSPOT",
    "band": "Auto",
    "client_count": 2,
    "max_clients": 8,
    "clients": [
        {"mac": "AA-BB-CC-DD-EE-01", "names": ["telephone-de-marie"]},
        {"mac": "aa:bb:cc:dd:ee:02", "names": []},
    ],
}

ARP_TEXT = """
Interface : 192.168.137.1 --- 0x14
  Adresse Internet      Adresse physique      Type
  192.168.137.12        aa-bb-cc-dd-ee-01     dynamique
  192.168.137.255       ff-ff-ff-ff-ff-ff     statique
  224.0.0.22            01-00-5e-00-00-16     statique
"""


def arp() -> str:
    return ARP_TEXT


# --- lecture ------------------------------------------------------------------------


def test_read_state_parses_clients_and_normalizes_macs() -> None:
    state = hotspot.read_state(FakeRunner(SAMPLE), arp)
    assert state.is_on and state.ssid == "MON_HOTSPOT" and state.client_count == 2 and state.max_clients == 8
    assert [client.mac for client in state.clients] == ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"]
    assert state.clients[0].label == "telephone-de-marie"
    assert state.clients[1].label == "aa:bb:cc:dd:ee:02"  # sans nom : on affiche l'adresse MAC
    assert state.clients[0].ip == "192.168.137.12"
    assert state.clients[1].ip is None  # absent de la table ARP : affiché sans adresse IP


def test_single_client_written_as_object_is_still_read() -> None:
    reply = {**SAMPLE, "client_count": 1, "clients": SAMPLE["clients"][0]}  # PowerShell 5.1 : tableau d'un élément
    assert len(hotspot.read_state(FakeRunner(reply), arp).clients) == 1


def test_ip_addresses_are_not_mistaken_for_device_names() -> None:
    reply = {**SAMPLE, "clients": [{"mac": "AA-BB-CC-DD-EE-01", "names": ["Redmi-13C", "192.168.137.41", "fe80::1"]}]}
    state = hotspot.read_state(FakeRunner(reply), arp)
    assert state.clients[0].names == ("Redmi-13C",)
    only_ip = {**SAMPLE, "clients": [{"mac": "AA-BB-CC-DD-EE-01", "names": ["192.168.137.41"]}]}
    assert hotspot.read_state(FakeRunner(only_ip), arp).clients[0].label == "aa:bb:cc:dd:ee:01"  # retombe sur la MAC


def test_unreadable_mac_is_skipped_not_fatal() -> None:
    reply = {**SAMPLE, "clients": [{"mac": "pas-une-mac"}, SAMPLE["clients"][0]]}
    assert [c.mac for c in hotspot.read_state(FakeRunner(reply), arp).clients] == ["aa:bb:cc:dd:ee:01"]


def test_arp_is_not_read_when_nobody_is_connected() -> None:
    called: list[int] = []

    def spy() -> str:
        called.append(1)
        return ARP_TEXT

    hotspot.read_state(FakeRunner({**SAMPLE, "client_count": 0, "clients": []}), spy)
    assert called == []


def test_arp_parsing_french_english_and_noise() -> None:
    from wifimanager.neighbors import parse_arp

    english = "  192.168.137.41       da-f7-ed-12-34-56     dynamic\n  224.0.0.251  01-00-5e-00-00-fb  static\n"
    assert parse_arp(english) == {"da:f7:ed:12:34:56": "192.168.137.41"}
    assert parse_arp(ARP_TEXT) == {"aa:bb:cc:dd:ee:01": "192.168.137.12"}
    assert parse_arp("n'importe quoi") == {}


def test_arp_prefers_dynamic_over_static_entry() -> None:
    from wifimanager.neighbors import parse_arp

    text = "  10.0.0.5   aa-bb-cc-dd-ee-01   statique\n  192.168.137.12   aa-bb-cc-dd-ee-01   dynamique\n"
    assert parse_arp(text) == {"aa:bb:cc:dd:ee:01": "192.168.137.12"}


def test_no_internet_profile_means_hotspot_unavailable() -> None:
    state = hotspot.read_state(FakeRunner({"available": False, "reason": "Aucune connexion Internet à partager"}))
    assert not state.available and not state.is_on and "Internet" in state.reason


def test_off_hotspot_is_not_on() -> None:
    assert not hotspot.read_state(FakeRunner({**SAMPLE, "state": "Off"})).is_on


# --- écriture et secret -------------------------------------------------------------


def test_passphrase_never_appears_in_the_script() -> None:
    runner = FakeRunner({"ok": True})
    hotspot.set_credentials("MON_HOTSPOT", "k7mp-4nwx-q2ht", runner)
    assert all("k7mp-4nwx-q2ht" not in script for script in runner.scripts)
    assert runner.envs[0] == {"WIFIMANAGER_SSID": "MON_HOTSPOT", "WIFIMANAGER_PASSPHRASE": "k7mp-4nwx-q2ht"}


def test_failed_operation_raises_with_reason() -> None:
    with pytest.raises(PowerShellError, match="Démarrage du hotspot impossible : Hors de portée"):
        hotspot.start(FakeRunner({"ok": False, "error": "Hors de portée"}))


def test_stop_succeeds_quietly() -> None:
    hotspot.stop(FakeRunner({"ok": True, "status": "Success"}))


def test_invalid_credentials_are_refused_before_calling_powershell() -> None:
    runner = FakeRunner({"ok": True})
    with pytest.raises(ValueError):
        hotspot.set_credentials("MON_HOTSPOT", "court", runner)
    with pytest.raises(ValueError):
        hotspot.set_credentials("", "un-bon-mot-de-passe", runner)
    assert runner.scripts == []


# --- mots de passe et adresses -----------------------------------------------------


def test_generated_passphrase_is_valid_and_random() -> None:
    first, second = generate_passphrase(), generate_passphrase()
    assert validate_passphrase(first) == first
    assert first != second
    assert len(first) == 14 and first.count("-") == 2


@pytest.mark.parametrize("bad", ["court", "x" * 64, "mot de passe avec é accent"])
def test_bad_passphrases_are_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_passphrase(bad)


def test_ssid_limits() -> None:
    assert validate_ssid("a" * 32)
    with pytest.raises(ValueError):
        validate_ssid("a" * 33)
    with pytest.raises(ValueError):
        validate_ssid("é" * 17)  # 34 octets en UTF-8


@pytest.mark.parametrize("raw", ["AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee:ff", "aabb.ccdd.eeff", "AABBCCDDEEFF"])
def test_mac_normalization(raw: str) -> None:
    assert normalize_mac(raw) == "aa:bb:cc:dd:ee:ff"


def test_invalid_mac_raises() -> None:
    with pytest.raises(ValueError):
        normalize_mac("aa:bb:cc")
