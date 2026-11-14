"""Mise en forme des durées, des accès et des données."""

import pytest

from wifimanager.formatting import (
    access_text,
    duration_label,
    format_remaining,
    usage_text,
)


@pytest.mark.parametrize(
    "seconds, text",
    [
        (0, "moins d'une minute"),
        (59, "moins d'une minute"),
        (60, "1 min"),
        (45 * 60, "45 min"),
        (3600, "1 h 00 min"),
        (5 * 3600 + 42 * 60, "5 h 42 min"),
        (23 * 3600 + 59 * 60, "23 h 59 min"),
        (24 * 3600, "1 j"),
        (26 * 3600, "1 j 2 h"),
        (7 * 86400, "7 j"),
    ],
)
def test_format_remaining(seconds: int, text: str) -> None:
    assert format_remaining(seconds) == text


@pytest.mark.parametrize(
    "device, text",
    [
        ({"mode": "allowed"}, "Autorisé"),
        ({"mode": None}, "Autorisé"),
        ({"mode": "blocked", "reason": "bloqué par l'utilisateur"}, "Bloqué"),
        ({"mode": "blocked", "reason": "accès expiré"}, "Accès expiré"),
        ({"mode": "pending"}, "En attente de ton accord"),
        ({"mode": "temporary", "remaining": 7200}, "Temporaire : 2 h 00 min restant"),
        ({"mode": "temporary", "remaining": None}, "Temporaire : moins d'une minute restant"),
    ],
)
def test_access_text(device: dict, text: str) -> None:
    assert access_text(device) == text


def test_usage_text_flags_estimates() -> None:
    assert usage_text({"downloaded": 1_200_000_000, "uploaded": 80_000_000}) == "↓ 1,2 Go   ↑ 80 Mo"
    assert usage_text({"downloaded": 10, "uploaded": 0, "estimated": True}) == "↓ 10 o   ↑ 0 o (estimé)"
    assert usage_text({}) == "↓ 0 o   ↑ 0 o"


@pytest.mark.parametrize(
    "seconds, text", [(21600, "6 h"), (43200, "12 h"), (86400, "1 j"), (259200, "3 j"), (5400, "90 min")]
)
def test_duration_label(seconds: int, text: str) -> None:
    assert duration_label(seconds) == text
