"""Service : application des règles, sûreté en cas d'erreur, sauvegarde, requêtes. Faux pare-feu et faux hotspot."""

import json

import pytest

from wifimanager.policy import Policy
from wifimanager.ps import PowerShellError
from wifimanager.service import ServiceCore
from wifimanager.usage import Counters, UsageTracker
from wifimanager.wfp import Firewall, WfpError

A = "aa:bb:cc:dd:ee:01"
B = "aa:bb:cc:dd:ee:02"
IP_A = "192.168.137.41"
IP_B = "192.168.137.57"
GATEWAY = "192.168.137.1"
HOUR = 3600


class FakeEngine:
    def __init__(self) -> None:
        self.live: dict[int, str] = {}
        self.next_id = 0
        self.closed = False
        self.fail = False

    def open(self) -> None: ...

    def add_block(self, layer: str, field: str, ip: str, name: str) -> int:
        if self.fail:
            raise WfpError("panne simulée")
        self.next_id += 1
        self.live[self.next_id] = ip
        return self.next_id

    def delete(self, filter_id: int) -> None:
        del self.live[filter_id]

    def close(self) -> None:
        self.closed = True
        self.live.clear()

    @property
    def blocked_ips(self) -> set[str]:
        return set(self.live.values())


class FakeHotspot:
    def __init__(self) -> None:
        self.clients = {A: ("Redmi-13C", IP_A)}
        self.state = "On"
        self.broken = False

    def __call__(self, script: str, env: dict | None = None) -> str:
        if self.broken:
            raise PowerShellError("PowerShell ne répond plus")
        return json.dumps(
            {
                "available": True,
                "state": self.state,
                "ssid": "JEMK_PC",
                "band": "Auto",
                "client_count": len(self.clients),
                "max_clients": 8,
                "clients": [{"mac": mac, "names": [name]} for mac, (name, _) in self.clients.items()],
            }
        )

    def arp(self) -> str:
        return "".join(f"  {ip}   {mac.replace(':', '-')}   dynamique\n" for mac, (_, ip) in self.clients.items())


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def hotspot_sim() -> FakeHotspot:
    return FakeHotspot()


def make_core(hotspot_sim, engine, clock, tmp_path, counters=None, policy=None, usage=None) -> ServiceCore:
    return ServiceCore(
        hotspot_sim,
        hotspot_sim.arp,
        Firewall(engine),
        policy or Policy(clock),
        usage or UsageTracker(),
        tmp_path / "state.json",
        counters_reader=lambda: counters,
        clock=clock,
    )


@pytest.fixture
def core(hotspot_sim, engine, clock, tmp_path) -> ServiceCore:
    return make_core(hotspot_sim, engine, clock, tmp_path, counters=Counters(GATEWAY, 0, 0))


# --- blocage -------------------------------------------------------------------------------------


def test_blocked_device_gets_its_ip_blocked_and_unblocked(core, engine) -> None:
    core.tick()
    assert engine.blocked_ips == set()
    assert core.handle({"action": "block", "mac": A})["ok"] is True
    assert engine.blocked_ips == {IP_A}  # appliqué tout de suite, sans attendre la lecture suivante
    core.handle({"action": "allow", "mac": A})
    assert engine.blocked_ips == set()


def test_only_the_blocked_device_is_cut(core, engine, hotspot_sim) -> None:
    hotspot_sim.clients[B] = ("Tablette", IP_B)
    core.tick()
    core.handle({"action": "block", "mac": B})
    assert engine.blocked_ips == {IP_B}


def test_strict_mode_holds_a_new_device_until_approved(core, engine, hotspot_sim) -> None:
    core.tick()
    core.handle({"action": "set_default_mode", "mode": "strict"})
    hotspot_sim.clients[B] = ("Invité", IP_B)
    core.tick()
    assert engine.blocked_ips == {IP_B}  # retenu dès sa première apparition
    assert core.handle({"action": "allow", "mac": B})["ok"] is True
    assert engine.blocked_ips == set()


def test_temporary_access_expires_and_the_device_gets_blocked(core, engine, clock) -> None:
    core.tick()
    core.handle({"action": "allow_for", "mac": A, "seconds": 6 * HOUR})
    clock.now += 6 * HOUR - 1
    core.tick()
    assert engine.blocked_ips == set()
    clock.now += 2
    core.tick()
    assert engine.blocked_ips == {IP_A}


def test_guest_pass_is_claimed_by_the_next_new_device_and_then_expires(core, engine, hotspot_sim, clock) -> None:
    core.tick()
    core.handle({"action": "set_default_mode", "mode": "strict"})
    reply = core.handle({"action": "create_pass", "seconds": 12 * HOUR})
    assert reply["ok"] and reply["pass"]["duration"] == 12 * HOUR

    hotspot_sim.clients[B] = ("Invité", IP_B)
    core.tick()
    assert engine.blocked_ips == set()  # il a réclamé le pass : autorisé
    status = {d["mac"]: d for d in core.handle({"action": "status"})["devices"]}
    assert status[B]["mode"] == "temporary" and status[B]["remaining"] == 12 * HOUR

    clock.now += 12 * HOUR
    core.tick()
    assert engine.blocked_ips == {IP_B}


def test_blocked_device_that_leaves_is_released_and_blocked_again_on_return(core, engine, hotspot_sim) -> None:
    core.tick()
    core.handle({"action": "block", "mac": A})
    hotspot_sim.clients.clear()
    core.tick()
    assert engine.blocked_ips == set()  # absent : on ne garde pas une IP qui pourrait être réattribuée à un autre
    hotspot_sim.clients[A] = ("Redmi-13C", "192.168.137.99")  # il revient avec une autre adresse
    core.tick()
    assert engine.blocked_ips == {"192.168.137.99"}


# --- sûreté ------------------------------------------------------------------------------------------


def test_read_failure_never_unblocks_anyone(core, engine, hotspot_sim) -> None:
    core.tick()
    core.handle({"action": "block", "mac": A})
    hotspot_sim.broken = True
    core.tick()
    assert engine.blocked_ips == {IP_A}
    assert "Lecture du hotspot impossible" in core.handle({"action": "status"})["service"]["error"]


def test_gateway_is_never_blocked(core, engine, hotspot_sim) -> None:
    hotspot_sim.clients[A] = ("Bizarre", GATEWAY)  # un appareil qui prendrait l'adresse du PC
    core.tick()
    core.handle({"action": "block", "mac": A})
    assert engine.blocked_ips == set()
    assert "protégée" in core.handle({"action": "status"})["service"]["error"]


def test_firewall_failure_is_reported_not_fatal(core, engine) -> None:
    core.tick()
    engine.fail = True
    core.handle({"action": "block", "mac": A})
    assert "Pare-feu" in core.handle({"action": "status"})["service"]["error"]
    engine.fail = False
    core.tick()  # le prochain passage réessaie
    assert engine.blocked_ips == {IP_A} and core.handle({"action": "status"})["service"]["error"] is None


# --- sauvegarde ---------------------------------------------------------------------------------------


def test_rules_and_usage_survive_a_restart(hotspot_sim, engine, clock, tmp_path) -> None:
    first = make_core(hotspot_sim, engine, clock, tmp_path, counters=Counters(GATEWAY, 0, 0))
    first.tick()
    first.handle({"action": "block", "mac": A})
    first.usage.record(Counters(GATEWAY, 100, 900), [A])
    first.save()

    reloaded = ServiceCore.load(hotspot_sim, hotspot_sim.arp, Firewall(FakeEngine()), tmp_path / "state.json")
    assert reloaded.policy.is_blocked(A)
    assert reloaded.usage.total == 1000 and reloaded.usage.devices[A].downloaded == 900  # la consommation est conservée


def test_state_is_not_rewritten_when_nothing_changed(core, tmp_path) -> None:
    core.tick()
    path = tmp_path / "state.json"
    before = path.stat().st_mtime_ns
    core.tick()
    core.tick()
    assert path.stat().st_mtime_ns == before


def test_corrupt_state_file_starts_clean(hotspot_sim, tmp_path) -> None:
    (tmp_path / "state.json").write_text("{ pas du json", encoding="utf-8")
    loaded = ServiceCore.load(hotspot_sim, hotspot_sim.arp, Firewall(FakeEngine()), tmp_path / "state.json")
    assert loaded.policy.rules == {}


# --- demandes -----------------------------------------------------------------------------------------


def test_status_lists_connected_and_absent_devices_with_usage(core, hotspot_sim) -> None:
    hotspot_sim.clients[B] = ("Tablette", IP_B)
    core.tick()
    core.handle({"action": "block", "mac": B})
    core.usage.record(Counters(GATEWAY, 0, 0), [A])
    core.usage.record(Counters(GATEWAY, 10, 90), [A])
    hotspot_sim.clients.pop(B)
    core.tick()
    devices = {d["mac"]: d for d in core.handle({"action": "status"})["devices"]}
    assert devices[A]["connected"] and devices[A]["names"] == ["Redmi-13C"] and devices[A]["downloaded"] >= 0
    assert not devices[B]["connected"] and devices[B]["blocked"] and devices[B]["ip"] == IP_B  # dernière IP connue


@pytest.mark.parametrize(
    "request_",
    [
        {"action": "inconnue"},
        {"action": "block", "mac": "pas-une-mac"},
        {"action": "allow_for", "mac": A, "seconds": 5},
        {"action": "allow_for", "mac": A, "seconds": "3600"},
        {"action": "allow_for", "mac": A},
        {"action": "create_pass", "seconds": 10**9},
        {"action": "set_default_mode", "mode": "n'importe quoi"},
        {"action": "revoke_pass", "id": "absent"},
    ],
)
def test_invalid_requests_are_refused_with_a_message(core, request_) -> None:
    reply = core.handle(request_)
    assert reply["ok"] is False and reply["error"]


def test_close_closes_the_firewall(core, engine) -> None:
    core.tick()
    core.handle({"action": "block", "mac": A})
    core.close()
    assert engine.closed and engine.blocked_ips == set()
