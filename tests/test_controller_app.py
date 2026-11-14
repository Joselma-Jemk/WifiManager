"""Travailleur (ordres, surveillance) et fenêtre (affichage, noms, boutons), sans Windows ni vrai hotspot."""

import json

import pytest
from PySide6.QtWidgets import QApplication

from wifimanager.app import MainWindow, PasswordDialog, ShareDialog
from wifimanager.controller import HotspotWorker
from wifimanager.models import Client, HotspotState
from wifimanager.settings import Settings, settings_file
from wifimanager.watchdog import Watchdog, WatchdogConfig

PASSPHRASE = "k7mp-4nwx-q2ht"


class FakeHotspot:
    """Simule Windows : répond à la lecture, au démarrage, à l'arrêt et à la configuration, en gardant les scripts."""

    def __init__(self) -> None:
        self.state = "On"
        self.clients = [{"mac": "AA-BB-CC-DD-EE-01", "names": ["Redmi-13C"]}]
        self.scripts: list[str] = []
        self.fail: str | None = None

    def __call__(self, script: str, env: dict | None = None) -> str:
        self.scripts.append(script)
        if self.fail:
            raise_ = self.fail
            self.fail = None
            from wifimanager.ps import PowerShellError

            raise PowerShellError(raise_)
        if "StartTetheringAsync" in script:
            self.state = "On"
            return json.dumps({"ok": True, "status": "Success"})
        if "StopTetheringAsync" in script:
            self.state = "Off"
            return json.dumps({"ok": True, "status": "Success"})
        if "ConfigureAccessPointAsync" in script:
            return json.dumps({"ok": True})
        return json.dumps(
            {
                "available": True,
                "state": self.state,
                "ssid": "JEMK_PC",
                "band": "Auto",
                "client_count": len(self.clients),
                "max_clients": 8,
                "clients": self.clients,
            }
        )

    def called(self, marker: str) -> int:
        return sum(marker in script for script in self.scripts)


def arp() -> str:
    return "  192.168.137.41   aa-bb-cc-dd-ee-01   dynamique\n"


@pytest.fixture
def fake() -> FakeHotspot:
    return FakeHotspot()


def make_worker(fake: FakeHotspot, enabled: bool = False, delay: float = 0) -> HotspotWorker:
    watchdog = Watchdog(WatchdogConfig(enabled=enabled, delay_seconds=delay))
    return HotspotWorker(fake, arp, watchdog)


def collect(worker: HotspotWorker) -> dict[str, list]:
    seen: dict[str, list] = {"state": [], "message": [], "failed": [], "credentials": []}
    worker.stateChanged.connect(seen["state"].append)
    worker.message.connect(seen["message"].append)
    worker.failed.connect(seen["failed"].append)
    worker.credentialsApplied.connect(lambda ssid, pwd: seen["credentials"].append((ssid, pwd)))
    return seen


# --- travailleur ------------------------------------------------------------------------


def test_poll_emits_state_with_ip(qapp, fake) -> None:
    worker = make_worker(fake)
    seen = collect(worker)
    worker.poll()
    state = seen["state"][0]
    assert state.is_on and state.clients[0].label == "Redmi-13C" and state.clients[0].ip == "192.168.137.41"


def test_read_failure_is_reported_not_raised(qapp, fake) -> None:
    worker = make_worker(fake)
    seen = collect(worker)
    fake.fail = "PowerShell est introuvable"
    worker.poll()
    assert seen["state"] == [] and "Lecture du hotspot impossible" in seen["failed"][0]


def test_watchdog_restarts_a_hotspot_that_turned_off(qapp, fake) -> None:
    worker = make_worker(fake, enabled=True)
    seen = collect(worker)
    worker.poll()  # vu allumé
    fake.state = "Off"  # extinction involontaire
    worker.poll()
    assert fake.called("StartTetheringAsync") == 1
    assert any("relance automatique" in message for message in seen["message"])
    assert seen["state"][-1].is_on


def test_stop_by_the_user_is_never_undone(qapp, fake) -> None:
    worker = make_worker(fake, enabled=True)
    worker.poll()
    worker.stop_hotspot()
    worker.poll()
    worker.poll()
    assert fake.called("StartTetheringAsync") == 0
    assert fake.state == "Off"


def test_change_credentials_applies_then_restarts_and_keeps_the_secret_out_of_scripts(qapp, fake) -> None:
    worker = make_worker(fake, enabled=True)
    seen = collect(worker)
    worker.poll()
    worker.change_credentials("JEMK_PC", PASSPHRASE)
    assert fake.called("ConfigureAccessPointAsync") == 1
    assert fake.called("StopTetheringAsync") == 1 and fake.called("StartTetheringAsync") == 1
    assert all(PASSPHRASE not in script for script in fake.scripts)
    assert seen["credentials"] == [("JEMK_PC", PASSPHRASE)]
    assert seen["state"][-1].is_on


def test_invalid_password_is_rejected_before_touching_the_hotspot(qapp, fake) -> None:
    worker = make_worker(fake)
    seen = collect(worker)
    worker.poll()
    before = len(fake.scripts)
    worker.change_credentials("JEMK_PC", "court")
    assert "Mot de passe non changé" in seen["failed"][0]
    assert seen["credentials"] == []
    assert fake.called("ConfigureAccessPointAsync") == 0
    assert len(fake.scripts) == before + 1  # seulement la relecture de l'état


def test_failed_start_is_reported(qapp, fake) -> None:
    worker = make_worker(fake)
    seen = collect(worker)
    fake.state = "Off"
    worker.poll()
    fake.fail = "Hors de portée"
    worker.start_hotspot()
    assert "Hors de portée" in seen["failed"][0]


# --- fenêtre -------------------------------------------------------------------------------------


@pytest.fixture
def window(qapp, fake):
    settings = Settings()
    win = MainWindow(settings, make_worker(fake))
    yield win
    win.close()


def state_with(*clients: Client, on: bool = True) -> HotspotState:
    return HotspotState(
        available=True, state="On" if on else "Off", ssid="JEMK_PC", band="Auto", client_count=len(clients), max_clients=8,
        clients=clients,
    )


def test_window_shows_state_and_devices(window) -> None:
    window._on_state(state_with(Client("aa:bb:cc:dd:ee:01", ("Redmi-13C",), "192.168.137.41")))
    assert window._status.text() == "Hotspot allumé"
    assert "JEMK_PC" in window._details.text() and "1/8" in window._details.text()
    assert window._toggle.text() == "Arrêter le hotspot"
    assert window._table.rowCount() == 1
    assert [window._table.item(0, c).text() for c in range(5)] == [
        "Redmi-13C", "Redmi-13C", "192.168.137.41", "aa:bb:cc:dd:ee:01", "Connecté",
    ]


def test_off_state_offers_to_start(window) -> None:
    window._on_state(state_with(on=False))
    assert window._status.text() == "Hotspot éteint" and window._toggle.text() == "Démarrer le hotspot"


def test_unavailable_hotspot_disables_actions_and_says_why(window) -> None:
    window._on_state(HotspotState(available=False, reason="Aucune connexion Internet à partager"))
    assert "indisponible" in window._status.text()
    assert "Internet" in window._details.text()
    assert not window._toggle.isEnabled() and not window._password_button.isEnabled()


def test_renaming_a_device_is_saved(window) -> None:
    window._on_state(state_with(Client("aa:bb:cc:dd:ee:01", ("Redmi-13C",), "192.168.137.41")))
    window._table.item(0, 0).setText("Téléphone de Marie")
    assert Settings.load().device_name("aa:bb:cc:dd:ee:01") == "Téléphone de Marie"
    window._on_state(state_with(Client("aa:bb:cc:dd:ee:01", ("Redmi-13C",), "192.168.137.41")))
    assert window._table.item(0, 0).text() == "Téléphone de Marie"  # le nom choisi l'emporte sur le nom d'hôte


def test_refreshing_the_table_does_not_overwrite_saved_names(window) -> None:
    window._on_state(state_with(Client("aa:bb:cc:dd:ee:01", ("Redmi-13C",), None)))
    assert not settings_file().exists()  # le simple affichage n'écrit rien


def test_block_buttons_are_disabled_until_the_admin_service_exists(window) -> None:
    buttons = {b.text(): b for b in window.findChildren(type(window._toggle))}
    assert not buttons["Bloquer"].isEnabled() and not buttons["Accès temporaire…"].isEnabled()


def test_toggle_asks_before_stopping_and_starts_directly(window, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    requests: list[str] = []
    window.requestStart.connect(lambda: requests.append("start"))
    window.requestStop.connect(lambda: requests.append("stop"))

    window._on_state(state_with(on=False))
    window._toggle.click()
    assert requests == ["start"]

    window._on_state(state_with(on=True))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    window._toggle.click()
    assert requests == ["start"]  # refusé : rien n'est arrêté
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    window._toggle.click()
    assert requests == ["start", "stop"]


def test_auto_restart_checkbox_is_saved_and_forwarded(window) -> None:
    forwarded: list[bool] = []
    window.requestAutoRestart.connect(forwarded.append)
    window._auto_restart.setChecked(True)
    assert forwarded == [True] and Settings.load().auto_restart is True


# --- dialogues --------------------------------------------------------------------------------


def test_password_dialog_proposes_a_valid_password_and_refuses_a_bad_one(qapp) -> None:
    dialog = PasswordDialog(None, "JEMK_PC")
    ssid, passphrase = dialog.values()
    assert ssid == "JEMK_PC" and len(passphrase) == 14
    dialog._passphrase.setText("court")
    dialog._validate()
    assert "entre 8 et 63" in dialog._error.text() and dialog.result() != 1


def test_share_dialog_shows_a_qr_and_clears_the_clipboard_only_if_unchanged(qapp) -> None:
    dialog = ShareDialog(None, "JEMK_PC", PASSPHRASE)
    dialog._copy()
    assert QApplication.clipboard().text() == PASSPHRASE
    dialog._clear_clipboard()
    assert QApplication.clipboard().text() != PASSPHRASE

    dialog._copy()
    QApplication.clipboard().setText("autre chose")
    dialog._clear_clipboard()
    assert QApplication.clipboard().text() == "autre chose"  # ce que l'utilisateur a copié ensuite est préservé


def test_table_header_is_in_french(window) -> None:
    headers = [window._table.horizontalHeaderItem(c).text() for c in range(5)]
    assert headers == ["Appareil", "Nom d'hôte", "Adresse IP", "Adresse MAC", "Statut"]
