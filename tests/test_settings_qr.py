"""Réglages (fichier abîmé, noms d'appareils) et QR code (caractères spéciaux)."""

import json
from pathlib import Path

from wifimanager.qr import QUIET_ZONE, qr_matrix, render_qr, wifi_payload
from wifimanager.settings import MAX_POLL_SECONDS, Settings, data_dir, settings_file

# --- réglages -------------------------------------------------------------------------


def test_defaults_when_file_is_missing() -> None:
    assert Settings.load() == Settings()


def test_roundtrip_with_device_names() -> None:
    settings = Settings(auto_restart=True, poll_seconds=20)
    settings.rename_device("AA-BB-CC-DD-EE-01", "  Téléphone de Marie  ")
    settings.save()
    loaded = Settings.load()
    assert loaded.auto_restart is True and loaded.poll_seconds == 20
    assert loaded.device_name("aa:bb:cc:dd:ee:01") == "Téléphone de Marie"


def test_empty_name_erases_the_custom_name() -> None:
    settings = Settings()
    settings.rename_device("aa:bb:cc:dd:ee:01", "Marie")
    settings.rename_device("aa:bb:cc:dd:ee:01", "   ")
    assert settings.device_name("aa:bb:cc:dd:ee:01") is None


def test_corrupt_file_gives_defaults_instead_of_crashing() -> None:
    settings_file().parent.mkdir(parents=True, exist_ok=True)
    settings_file().write_text("{ pas du json", encoding="utf-8")
    assert Settings.load() == Settings()
    settings_file().write_text(json.dumps(["pas", "un", "dictionnaire"]), encoding="utf-8")
    assert Settings.load() == Settings()


def test_out_of_range_values_are_clamped_or_ignored() -> None:
    settings_file().parent.mkdir(parents=True, exist_ok=True)
    settings_file().write_text(
        json.dumps({"auto_restart": "oui", "poll_seconds": 99999, "device_names": {"pas-une-mac": "x", "aa:bb:cc:dd:ee:01": "ok"}}),
        encoding="utf-8",
    )
    loaded = Settings.load()
    assert loaded.auto_restart is False  # seul `true` active la relance
    assert loaded.poll_seconds == MAX_POLL_SECONDS
    assert loaded.device_names == {"aa:bb:cc:dd:ee:01": "ok"}


def test_data_dir_is_overridable_and_save_is_atomic(tmp_path: Path) -> None:
    assert data_dir() == tmp_path / "data"
    Settings(auto_restart=True).save()
    assert not list((tmp_path / "data").glob("*.tmp"))


# --- QR code ------------------------------------------------------------------------------


def test_wifi_payload_escapes_special_characters() -> None:
    assert wifi_payload("Mon;Wifi", 'a:b,c\\d"e') == 'WIFI:T:WPA;S:Mon\\;Wifi;P:a\\:b\\,c\\\\d\\"e;;'


def test_plain_payload() -> None:
    assert wifi_payload("JEMK_PC", "k7mp-4nwx-q2ht") == "WIFI:T:WPA;S:JEMK_PC;P:k7mp-4nwx-q2ht;;"


def test_matrix_is_square_with_a_finder_pattern_corner() -> None:
    matrix = qr_matrix(wifi_payload("JEMK_PC", "k7mp-4nwx-q2ht"))
    assert len(matrix) == len(matrix[0]) >= 21
    assert matrix[0][0] is True  # coin d'un des trois repères de positionnement


def test_rendered_pixmap_has_quiet_zone_and_dark_modules(qapp) -> None:
    pixmap = render_qr(wifi_payload("A", "motdepasse1"), size=300)
    image = pixmap.toImage()
    assert pixmap.width() == pixmap.height()
    assert image.pixelColor(0, 0).name() == "#ffffff"  # zone blanche autour du code
    scale = pixmap.width() // (len(qr_matrix(wifi_payload("A", "motdepasse1"))) + 2 * QUIET_ZONE)
    assert image.pixelColor(QUIET_ZONE * scale + 1, QUIET_ZONE * scale + 1).name() == "#000000"
