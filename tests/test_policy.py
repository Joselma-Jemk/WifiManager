"""Règles d'accès : blocage, accès temporaires, pass invités, mode strict, sauvegarde."""

import json

import pytest

from wifimanager.policy import (
    ALLOWED,
    BLOCKED,
    OPEN,
    PENDING,
    STRICT,
    Policy,
    PolicyError,
)

A = "aa:bb:cc:dd:ee:01"
B = "aa:bb:cc:dd:ee:02"
C = "aa:bb:cc:dd:ee:03"
HOUR = 3600


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def policy(clock: Clock) -> Policy:
    return Policy(clock)


# --- blocage et accès temporaires ------------------------------------------------------------


def test_device_without_rule_is_allowed_in_open_mode(policy) -> None:
    assert not policy.is_blocked(A)
    assert policy.status(A)["mode"] == ALLOWED and policy.status(A)["explicit"] is False


def test_block_then_allow(policy) -> None:
    policy.block(A)
    assert policy.is_blocked(A) and policy.status(A)["mode"] == BLOCKED
    policy.allow(A)
    assert not policy.is_blocked(A)


def test_mac_formats_are_equivalent(policy) -> None:
    policy.block("AA-BB-CC-DD-EE-01")
    assert policy.is_blocked("aa:bb:cc:dd:ee:01")


@pytest.mark.parametrize("hours", [6, 12, 24])
def test_temporary_access_lasts_exactly_its_duration_then_blocks(policy, clock, hours) -> None:
    policy.allow_for(A, hours * HOUR)
    assert not policy.is_blocked(A)
    assert policy.status(A)["remaining"] == hours * HOUR

    clock.now += hours * HOUR - 1
    assert not policy.is_blocked(A)
    clock.now += 1
    assert policy.is_blocked(A)  # bloqué dès l'échéance, même avant le prochain passage d'observe()

    events = policy.observe([A])
    assert [e.kind for e in events] == ["access_expired"]
    assert policy.status(A)["mode"] == BLOCKED and policy.status(A)["reason"] == "accès expiré"


def test_expired_device_stays_blocked_afterwards(policy, clock) -> None:
    policy.allow_for(A, 6 * HOUR)
    clock.now += 7 * HOUR
    policy.observe([A])
    policy.observe([A])
    assert policy.is_blocked(A)


@pytest.mark.parametrize("seconds", [0, 59, MAX := 7 * 24 * 3600 + 1, -5, True, 1.5, "3600"])
def test_invalid_durations_are_refused(policy, seconds) -> None:
    with pytest.raises(PolicyError):
        policy.allow_for(A, seconds)


def test_forget_returns_to_default_behaviour(policy) -> None:
    policy.block(A)
    policy.forget(A)
    assert not policy.is_blocked(A)


# --- mode strict -------------------------------------------------------------------------------


def test_strict_mode_holds_new_devices_until_approved(policy) -> None:
    policy.set_default_mode(STRICT)
    events = policy.observe([A])
    assert [e.kind for e in events] == ["new_device_held"]
    assert policy.is_blocked(A) and policy.status(A)["mode"] == PENDING
    policy.allow(A)
    assert not policy.is_blocked(A)


def test_open_mode_only_reports_new_devices(policy) -> None:
    assert [e.kind for e in policy.observe([A])] == ["new_device"]
    assert not policy.is_blocked(A)
    assert policy.observe([A]) == []  # plus nouveau


def test_switching_to_strict_never_cuts_devices_already_known(policy) -> None:
    policy.observe([A, B])
    policy.set_default_mode(STRICT)
    assert not policy.is_blocked(A) and not policy.is_blocked(B)
    assert [e.kind for e in policy.observe([A, B, C])] == ["new_device_held"]  # seul le nouveau est retenu
    assert policy.is_blocked(C)


def test_explicit_block_survives_switching_modes(policy) -> None:
    policy.observe([A])
    policy.block(A)
    policy.set_default_mode(STRICT)
    assert policy.is_blocked(A)


def test_unknown_mode_is_refused(policy) -> None:
    with pytest.raises(PolicyError):
        policy.set_default_mode("n'importe quoi")


# --- pass invités -------------------------------------------------------------------------------


def test_new_device_claims_a_valid_pass_and_gets_timed_access(policy, clock) -> None:
    policy.set_default_mode(STRICT)
    guest = policy.create_pass(12 * HOUR)
    events = policy.observe([A])
    assert [e.kind for e in events] == ["pass_claimed"] and events[0].detail == guest.id
    assert not policy.is_blocked(A)
    assert policy.status(A)["remaining"] == 12 * HOUR
    clock.now += 12 * HOUR
    assert policy.is_blocked(A)  # le pass s'éteint avec l'accès


def test_a_pass_can_only_be_claimed_once(policy) -> None:
    policy.set_default_mode(STRICT)
    policy.create_pass(6 * HOUR)
    policy.observe([A])
    policy.observe([A, B])
    assert not policy.is_blocked(A)
    assert policy.is_blocked(B)  # le pass est déjà pris : B est retenu comme tout nouvel appareil


def test_known_device_cannot_claim_a_pass(policy) -> None:
    policy.set_default_mode(STRICT)
    policy.observe([A])
    policy.allow(A)
    policy.create_pass(6 * HOUR)
    policy.observe([A])
    assert policy.status(A)["mode"] == ALLOWED  # un appareil déjà connu ne voit pas son accès réduit
    assert len(policy.pending_passes()) == 1


def test_unclaimed_pass_expires_after_its_claim_window(policy, clock) -> None:
    policy.set_default_mode(STRICT)
    policy.create_pass(6 * HOUR, claim_seconds=600)
    clock.now += 601
    events = policy.observe([A])
    assert "pass_expired" in [e.kind for e in events]
    assert policy.is_blocked(A)  # le pass a disparu : A est un nouvel appareil ordinaire, retenu


def test_oldest_pass_is_claimed_first(policy, clock) -> None:
    policy.set_default_mode(STRICT)
    first = policy.create_pass(6 * HOUR)
    clock.now += 10
    policy.create_pass(24 * HOUR)
    policy.observe([A])
    assert policy.status(A)["remaining"] == 6 * HOUR and policy.rules[A].pass_id == first.id


def test_revoking_a_claimed_pass_blocks_its_device(policy) -> None:
    policy.set_default_mode(STRICT)
    guest = policy.create_pass(24 * HOUR)
    policy.observe([A])
    policy.revoke_pass(guest.id)
    assert policy.is_blocked(A) and policy.status(A)["reason"] == "pass invité révoqué"


def test_revoking_an_unclaimed_pass_removes_it(policy) -> None:
    guest = policy.create_pass(6 * HOUR)
    policy.revoke_pass(guest.id)
    assert policy.pending_passes() == []
    with pytest.raises(PolicyError):
        policy.revoke_pass(guest.id)


def test_pass_limits(policy) -> None:
    with pytest.raises(PolicyError):
        policy.create_pass(30)
    with pytest.raises(PolicyError):
        policy.create_pass(HOUR, claim_seconds=10)
    for _ in range(20):
        policy.create_pass(HOUR)
    with pytest.raises(PolicyError, match="Trop de pass"):
        policy.create_pass(HOUR)


# --- sauvegarde -----------------------------------------------------------------------------------


def test_roundtrip_keeps_rules_passes_and_expiry(policy, clock) -> None:
    policy.set_default_mode(STRICT)
    policy.observe([A])
    policy.allow_for(A, 6 * HOUR)
    policy.block(B)
    guest = policy.create_pass(24 * HOUR)
    restored = Policy.from_dict(json.loads(json.dumps(policy.to_dict())), clock)
    assert restored.default_mode == STRICT
    assert restored.status(A)["remaining"] == 6 * HOUR and restored.is_blocked(B)
    assert restored.pending_passes()[0].id == guest.id
    assert A in restored.known and B in restored.known


def test_expiry_continues_across_a_restart(policy, clock) -> None:
    policy.allow_for(A, HOUR)
    saved = policy.to_dict()
    clock.now += 2 * HOUR  # le service était arrêté pendant l'échéance
    restored = Policy.from_dict(saved, clock)
    assert restored.is_blocked(A)
    assert [e.kind for e in restored.observe([A])] == ["access_expired"]


@pytest.mark.parametrize(
    "garbage",
    [
        None,
        "pas un dictionnaire",
        ["liste"],
        {"rules": "oups", "passes": 3, "known": "x", "default_mode": 12},
        {"rules": {"pas-une-mac": {"mode": "blocked"}, A: {"mode": "inconnu"}, B: {"mode": "temporary", "expires_at": "x"}}},
        {"passes": {"p1": {"duration": 5, "created_at": 1, "claim_deadline": 2}}},
    ],
)
def test_corrupt_saved_state_is_ignored_not_fatal(garbage, clock) -> None:
    restored = Policy.from_dict(garbage, clock)
    assert restored.rules == {} and restored.passes == {} and restored.default_mode == OPEN


def test_one_bad_rule_does_not_lose_the_good_ones(clock) -> None:
    restored = Policy.from_dict({"rules": {"pas-une-mac": {"mode": "blocked"}, A: {"mode": "blocked"}}}, clock)
    assert restored.is_blocked(A)
