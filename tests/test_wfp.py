"""Logique de blocage du pare-feu, avec un faux moteur (le vrai demande les droits administrateur)."""

import ctypes

import pytest

from wifimanager import wfp
from wifimanager.wfp import Firewall, WfpError, check_blockable


class FakeEngine:
    def __init__(self, fail_on: int | None = None) -> None:
        self.opened = False
        self.closed = False
        self.next_id = 100
        self.live: dict[int, tuple[str, str]] = {}
        self.fail_on = fail_on  # le n-ième ajout échoue
        self.adds = 0
        self.fail_delete: set[int] = set()

    def open(self) -> None:
        self.opened = True

    def add_block(self, layer: str, field: str, ip: str, name: str) -> int:
        self.adds += 1
        if self.fail_on == self.adds:
            raise WfpError("échec simulé")
        self.next_id += 1
        self.live[self.next_id] = (layer, ip)
        return self.next_id

    def delete(self, filter_id: int) -> None:
        if filter_id in self.fail_delete:
            raise WfpError("retrait refusé")
        del self.live[filter_id]

    def close(self) -> None:
        self.closed = True
        self.live.clear()


def test_block_adds_two_filters_one_for_each_direction() -> None:
    engine = FakeEngine()
    firewall = Firewall(engine)
    firewall.block("192.168.137.41")
    assert sorted(layer for layer, _ in engine.live.values()) == sorted([wfp.LAYER_INBOUND_IPPACKET_V4, wfp.LAYER_IPFORWARD_V4])
    assert firewall.blocked == {"192.168.137.41"}


def test_blocking_twice_does_not_add_filters_again() -> None:
    engine = FakeEngine()
    firewall = Firewall(engine)
    firewall.block("192.168.137.41")
    firewall.block("192.168.137.41")
    assert len(engine.live) == 2


def test_unblock_removes_both_filters() -> None:
    engine = FakeEngine()
    firewall = Firewall(engine)
    firewall.block("192.168.137.41")
    firewall.unblock("192.168.137.41")
    assert engine.live == {} and firewall.blocked == frozenset()
    firewall.unblock("192.168.137.41")  # idempotent


def test_half_installed_block_is_rolled_back() -> None:
    engine = FakeEngine(fail_on=2)  # le second filtre échoue
    firewall = Firewall(engine)
    with pytest.raises(WfpError):
        firewall.block("192.168.137.41")
    assert engine.live == {} and firewall.blocked == frozenset()  # pas de blocage à moitié posé


def test_failed_removal_is_remembered_and_retried() -> None:
    engine = FakeEngine()
    firewall = Firewall(engine)
    firewall.block("192.168.137.41")
    first_id = next(iter(engine.live))
    engine.fail_delete = {first_id}
    with pytest.raises(WfpError):
        firewall.unblock("192.168.137.41")
    assert "192.168.137.41" in firewall.blocked  # toujours bloqué : on ne prétend pas le contraire
    engine.fail_delete = set()
    firewall.unblock("192.168.137.41")
    assert engine.live == {} and firewall.blocked == frozenset()


def test_sync_adds_and_removes_to_match_the_wanted_set() -> None:
    firewall = Firewall(FakeEngine())
    firewall.block("192.168.137.10")
    firewall.block("192.168.137.11")
    added, removed = firewall.sync({"192.168.137.11", "192.168.137.12"})
    assert added == {"192.168.137.12"} and removed == {"192.168.137.10"}
    assert firewall.blocked == {"192.168.137.11", "192.168.137.12"}


def test_sync_continues_after_one_failure_and_reports_it() -> None:
    engine = FakeEngine()
    firewall = Firewall(engine, protected={"192.168.137.1"})
    with pytest.raises(WfpError, match="protégée"):
        firewall.sync({"192.168.137.1", "192.168.137.20"})
    assert firewall.blocked == {"192.168.137.20"}  # l'adresse valide a quand même été bloquée


@pytest.mark.parametrize(
    "ip",
    ["8.8.8.8", "127.0.0.1", "169.254.1.5", "224.0.0.1", "pas-une-ip", "::1", "fe80::1", ""],
)
def test_never_blocks_public_local_or_malformed_addresses(ip: str) -> None:
    with pytest.raises(WfpError):
        check_blockable(ip)


def test_protected_addresses_are_refused() -> None:
    with pytest.raises(WfpError, match="protégée"):
        check_blockable("192.168.137.1", protected={"192.168.137.1"})
    assert check_blockable("192.168.137.41", protected={"192.168.137.1"}) == "192.168.137.41"


def test_close_removes_everything() -> None:
    engine = FakeEngine()
    firewall = Firewall(engine)
    firewall.block("192.168.137.41")
    firewall.close()
    assert engine.closed and engine.live == {} and firewall.blocked == frozenset()


def test_native_structure_layout_matches_windows() -> None:
    """Disposition vérifiée par une sonde réelle : un décalage ferait échouer ou corrompre les appels."""
    assert ctypes.sizeof(wfp._Filter) == 200
    assert ctypes.sizeof(wfp._Condition) == 40
    assert ctypes.sizeof(wfp._SubLayer) == 72
    assert ctypes.sizeof(wfp._Session) == 72
