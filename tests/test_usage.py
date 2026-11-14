"""Consommation de données : attribution, redémarrage des compteurs, sauvegarde, formatage."""

import json

import pytest

from wifimanager.usage import Counters, UsageTracker, format_bytes, read_counters

A = "aa:bb:cc:dd:ee:01"
B = "aa:bb:cc:dd:ee:02"
C = "aa:bb:cc:dd:ee:03"


def counters(received: int, sent: int) -> Counters:
    return Counters(ip="192.168.137.1", received=received, sent=sent)


def test_first_reading_is_only_a_starting_point() -> None:
    tracker = UsageTracker()
    tracker.record(counters(1000, 5000), [A])
    assert tracker.total == 0 and tracker.devices == {}


def test_single_device_gets_everything_exactly() -> None:
    tracker = UsageTracker()
    tracker.record(counters(1000, 5000), [A])
    tracker.record(counters(1400, 9000), [A])
    usage = tracker.devices[A]
    assert (usage.uploaded, usage.downloaded) == (400, 4000)
    assert usage.estimated is False
    assert tracker.uploaded == 400 and tracker.downloaded == 4000 and tracker.unattributed == 0


def test_several_devices_share_equally_and_are_marked_estimated() -> None:
    tracker = UsageTracker()
    tracker.record(counters(0, 0), [A, B])
    tracker.record(counters(1000, 3000), [A, B])
    assert (tracker.devices[A].uploaded, tracker.devices[B].uploaded) == (500, 500)
    assert (tracker.devices[A].downloaded, tracker.devices[B].downloaded) == (1500, 1500)
    assert tracker.devices[A].estimated and tracker.devices[B].estimated


def test_division_remainder_is_not_lost() -> None:
    tracker = UsageTracker()
    tracker.record(counters(0, 0), [A, B, C])
    tracker.record(counters(10, 11), [A, B, C])
    assert sum(d.uploaded for d in tracker.devices.values()) == 10
    assert sum(d.downloaded for d in tracker.devices.values()) == 11


def test_traffic_without_a_readable_device_stays_unattributed() -> None:
    tracker = UsageTracker()
    tracker.record(counters(0, 0), [])
    tracker.record(counters(300, 700), [])
    assert tracker.unattributed == 1000 and tracker.devices == {} and tracker.total == 1000


def test_counter_reset_after_adapter_restart_counts_what_is_displayed() -> None:
    tracker = UsageTracker()
    tracker.record(counters(1_000_000, 5_000_000), [A])
    tracker.record(counters(2000, 9000), [A])  # le hotspot a redémarré : les compteurs repartent de zéro
    assert tracker.devices[A].uploaded == 2000 and tracker.devices[A].downloaded == 9000


def test_idle_interval_changes_nothing() -> None:
    tracker = UsageTracker()
    tracker.record(counters(100, 100), [A])
    tracker.record(counters(100, 100), [A])
    assert tracker.devices == {}


def test_mac_formats_are_normalized() -> None:
    tracker = UsageTracker()
    tracker.record(counters(0, 0), ["AA-BB-CC-DD-EE-01"])
    tracker.record(counters(10, 10), ["AA-BB-CC-DD-EE-01"])
    assert A in tracker.devices


def test_reset_clears_everything() -> None:
    tracker = UsageTracker()
    tracker.record(counters(0, 0), [A])
    tracker.record(counters(10, 10), [A])
    tracker.reset()
    assert tracker.total == 0 and tracker.devices == {}


def test_roundtrip_and_corrupt_data() -> None:
    tracker = UsageTracker()
    tracker.record(counters(0, 0), [A, B])
    tracker.record(counters(100, 300), [A, B])
    restored = UsageTracker.from_dict(json.loads(json.dumps(tracker.to_dict())))
    assert restored.devices[A].downloaded == 150 and restored.devices[A].estimated and restored.total == 400
    for garbage in (None, "x", [], {"devices": "x", "uploaded": -5, "downloaded": "beaucoup"}):
        assert UsageTracker.from_dict(garbage).total == 0


def test_read_counters_parses_the_adapter_reply() -> None:
    reply = json.dumps({"found": True, "ip": "192.168.137.1", "received": 40260460, "sent": 550150303})
    assert read_counters(lambda script, env=None: reply) == Counters("192.168.137.1", 40260460, 550150303)


def test_read_counters_without_adapter_or_powershell_gives_none() -> None:
    assert read_counters(lambda script, env=None: json.dumps({"found": False})) is None

    def broken(script, env=None):
        from wifimanager.ps import PowerShellError

        raise PowerShellError("introuvable")

    assert read_counters(broken) is None


@pytest.mark.parametrize(
    "count, text",
    [(0, "0 o"), (999, "999 o"), (12_000, "12 Ko"), (1_400_000, "1,4 Mo"), (530_000_000, "530 Mo"), (2_500_000_000, "2,5 Go")],
)
def test_format_bytes(count: int, text: str) -> None:
    assert format_bytes(count) == text
