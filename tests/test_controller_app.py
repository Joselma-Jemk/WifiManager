"""Travailleur (ordres, surveillance, service) et fenêtre (affichage, blocage, pass), sans Windows ni vrai hotspot."""

import json
import time

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from wifimanager.app import MainWindow
from wifimanager.controller import HotspotWorker
from wifimanager.dialogs import DurationDialog, PassDialog, PasswordDialog, ShareDialog
from wifimanager.models import Client, HotspotState
from wifimanager.ps import PowerShellError
from wifimanager.service_client import ServiceUnavailable
from wifimanager.settings import Settings, settings_file
from wifimanager.watchdog import Watchdog, WatchdogConfig

PASSPHRASE = "k7mp-4nwx-q2ht"
A = "aa:bb:cc:dd:ee:01"
B = "aa:bb:cc:dd:ee:02"


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
            message, self.fail = self.fail, None
            raise PowerShellError(message)
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


class FakeService:
    """Simule le service : garde les ordres reçus, répond à l'état avec ce qu'on lui a donné."""

    def __init__(self) -> None:
        self.available = True
        self.orders: list[dict] = []
        self.status = service_status()
        self.replies: dict[str, dict] = {}

    def __call__(self, payload: dict) -> dict:
        if not self.available:
            raise ServiceUnavailable("Le service de blocage n'est pas lancé")
        if payload["action"] == "status":
            return self.status
        self.orders.append(payload)
        return self.replies.get(payload["action"], {"ok": True})


def device(mac: str = A, **overrides) -> dict:
    base = {
        "mac": mac, "connected": True, "ip": "192.168.137.41", "names": ["Redmi-13C"], "mode": "allowed",
        "explicit": False, "remaining": None, "reason": "", "blocked": False, "uploaded": 80_000_000,
        "downloaded": 1_200_000_000, "estimated": False,
    }
    return {**base, **overrides}


def service_status(devices=None, default_mode="open", passes=None, events=None, error=None) -> dict:
    return {
        "ok": True,
        "service": {"version": 1, "blocking": True, "last_tick": 1.0, "error": error},
        "default_mode": default_mode,
        "devices": devices if devices is not None else [device()],
        "passes": passes or [],
        "events": events or [],
        "usage": {"since": 1_700_000_000.0, "uploaded": 80_000_000, "downloaded": 1_200_000_000, "unattributed": 0},
    }


@pytest.fixture
def fake() -> FakeHotspot:
    return FakeHotspot()


@pytest.fixture
def service() -> FakeService:
    return FakeService()


def make_worker(fake, service=None, enabled: bool = False, delay: float = 0, **kwargs) -> HotspotWorker:
    watchdog = Watchdog(WatchdogConfig(enabled=enabled, delay_seconds=delay))
    service = service or FakeService()
    return HotspotWorker(fake, arp, watchdog, service_call=service, **kwargs)


def collect(worker: HotspotWorker) -> dict[str, list]:
    seen: dict[str, list] = {"state": [], "message": [], "failed": [], "credentials": [], "service": [], "pass": []}
    worker.stateChanged.connect(seen["state"].append)
    worker.message.connect(seen["message"].append)
    worker.failed.connect(seen["failed"].append)
    worker.credentialsApplied.connect(lambda ssid, pwd: seen["credentials"].append((ssid, pwd)))
    worker.serviceChanged.connect(seen["service"].append)
    worker.passCreated.connect(seen["pass"].append)
    return seen


# --- travailleur : hotspot ---------------------------------------------------------------------


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
    worker.poll()
    fake.state = "Off"
    worker.poll()
    assert fake.called("StartTetheringAsync") == 1
    assert any("relance automatique" in message for message in seen["message"])


def test_stop_by_the_user_is_never_undone(qapp, fake) -> None:
    worker = make_worker(fake, enabled=True)
    worker.poll()
    worker.stop_hotspot()
    worker.poll()
    worker.poll()
    assert fake.called("StartTetheringAsync") == 0 and fake.state == "Off"


def test_change_credentials_applies_then_restarts_and_keeps_the_secret_out_of_scripts(qapp, fake) -> None:
    worker = make_worker(fake, enabled=True)
    seen = collect(worker)
    worker.poll()
    worker.change_credentials("JEMK_PC", PASSPHRASE)
    assert fake.called("ConfigureAccessPointAsync") == 1
    assert fake.called("StopTetheringAsync") == 1 and fake.called("StartTetheringAsync") == 1
    assert all(PASSPHRASE not in script for script in fake.scripts)
    assert seen["credentials"] == [("JEMK_PC", PASSPHRASE)]


def test_invalid_password_is_rejected_before_touching_the_hotspot(qapp, fake) -> None:
    worker = make_worker(fake)
    seen = collect(worker)
    worker.poll()
    worker.change_credentials("JEMK_PC", "court")
    assert "Mot de passe non changé" in seen["failed"][0]
    assert seen["credentials"] == [] and fake.called("ConfigureAccessPointAsync") == 0


# --- travailleur : service -------------------------------------------------------------------------


def test_poll_reports_the_service_status_or_its_absence(qapp, fake, service) -> None:
    worker = make_worker(fake, service)
    seen = collect(worker)
    worker.poll()
    assert seen["service"][-1]["devices"][0]["mac"] == A
    service.available = False
    worker.poll()
    assert seen["service"][-1] is None


def test_service_action_is_forwarded_and_status_refreshed(qapp, fake, service) -> None:
    worker = make_worker(fake, service)
    seen = collect(worker)
    worker.service_action({"action": "block", "mac": A})
    assert service.orders == [{"action": "block", "mac": A}]
    assert seen["service"] and seen["failed"] == []


def test_refused_order_is_reported(qapp, fake, service) -> None:
    worker = make_worker(fake, service)
    seen = collect(worker)
    service.replies["allow_for"] = {"ok": False, "error": "La durée doit être comprise entre 60 s et 168 h"}
    worker.service_action({"action": "allow_for", "mac": A, "seconds": 5})
    assert "La durée doit être" in seen["failed"][0]


def test_order_without_service_is_reported(qapp, fake, service) -> None:
    worker = make_worker(fake, service)
    seen = collect(worker)
    service.available = False
    worker.service_action({"action": "block", "mac": A})
    assert "pas lancé" in seen["failed"][0]


def test_created_pass_is_announced(qapp, fake, service) -> None:
    worker = make_worker(fake, service)
    seen = collect(worker)
    service.replies["create_pass"] = {"ok": True, "pass": {"id": "ab12", "duration": 43200, "claim_deadline": time.time() + 1800}}
    worker.service_action({"action": "create_pass", "seconds": 43200})
    assert seen["pass"][0]["id"] == "ab12"


def test_install_waits_for_the_service_to_answer(qapp, fake, service) -> None:
    service.available = False
    calls: list[str] = []

    def install() -> None:
        calls.append("install")
        service.available = True  # le service démarre une fois la tâche enregistrée

    worker = make_worker(fake, service, install_fn=install)
    seen = collect(worker)
    worker.install_service()
    assert calls == ["install"] and any("Blocage activé" in m for m in seen["message"])


def test_refused_install_is_reported(qapp, fake, service) -> None:
    service.available = False

    def refuse() -> None:
        raise PowerShellError("Autorisation administrateur refusée")

    worker = make_worker(fake, service, install_fn=refuse)
    seen = collect(worker)
    worker.install_service()
    assert "Activation du blocage impossible" in seen["failed"][0] and "refusée" in seen["failed"][0]


def test_uninstall_shuts_the_service_down_then_removes_the_task(qapp, fake, service) -> None:
    removed: list[str] = []
    worker = make_worker(fake, service, uninstall_fn=lambda: removed.append("ok"))
    seen = collect(worker)
    worker.uninstall_service()
    assert service.orders == [{"action": "shutdown"}] and removed == ["ok"]
    assert any("Blocage désactivé" in m for m in seen["message"])


# --- fenêtre ---------------------------------------------------------------------------------------


@pytest.fixture
def window(qapp, fake, service):
    win = MainWindow(Settings(), make_worker(fake, service))
    yield win
    win.close()


def state_with(*clients: Client, on: bool = True) -> HotspotState:
    return HotspotState(
        available=True, state="On" if on else "Off", ssid="JEMK_PC", band="Auto", client_count=len(clients), max_clients=8,
        clients=clients,
    )


def cells(window, row: int = 0) -> list[str]:
    return [window._table.item(row, c).text() for c in range(window._table.columnCount())]


def test_window_without_service_shows_devices_and_asks_to_enable_blocking(window) -> None:
    window._on_state(state_with(Client(A, ("Redmi-13C",), "192.168.137.41")))
    assert window._status.text() == "Hotspot allumé" and "1/8" in window._details.text()
    assert cells(window) == ["Redmi-13C", "192.168.137.41", A, "—", "—", "Connecté"]
    assert not window._install_button.isHidden() and window._uninstall_button.isHidden()
    assert not window._pass_button.isEnabled() and not window._block_button.isEnabled()
    assert "Active d'abord" in window._block_button.toolTip()


def test_window_with_service_shows_access_and_data(window) -> None:
    window._on_state(state_with(Client(A, ("Redmi-13C",), "192.168.137.41")))
    window._on_service(service_status([device(), device(B, connected=False, names=[], ip="192.168.137.57", mode="blocked", blocked=True, uploaded=0, downloaded=5000)]))
    assert window._table.rowCount() == 2  # aussi l'appareil parti, car il a une règle
    assert cells(window, 0)[3:] == ["Autorisé", "↓ 1,2 Go   ↑ 80 Mo", "Connecté"]
    assert cells(window, 1)[3:] == ["Bloqué", "↓ 5 Ko   ↑ 0 o", "Absent"]
    assert not window._install_button.isVisible() and not window._uninstall_button.isHidden()
    assert "Blocage actif" in window._service_label.text()
    assert "↓ 1,2 Go" in window._usage_label.text() and "total 1,3 Go" in window._usage_label.text()


def test_temporary_pending_and_expired_access_texts(window) -> None:
    window._on_service(
        service_status(
            [
                device(A, mode="temporary", remaining=5 * 3600 + 42 * 60, explicit=True),
                device(B, mode="pending", blocked=True, explicit=True),
                device("aa:bb:cc:dd:ee:03", mode="blocked", reason="accès expiré", blocked=True, explicit=True),
            ]
        )
    )
    assert [cells(window, r)[3] for r in range(3)] == ["Temporaire : 5 h 42 min restant", "En attente de ton accord", "Accès expiré"]


def test_estimated_data_is_flagged(window) -> None:
    window._on_service(service_status([device(estimated=True)]))
    assert cells(window)[4].endswith("(estimé)")


def test_buttons_follow_the_selected_device(window) -> None:
    window._on_service(service_status([device(A), device(B, mode="blocked", blocked=True, explicit=True)]))
    window._table.selectRow(0)
    assert window._block_button.isEnabled() and not window._allow_button.isEnabled() and not window._forget_button.isEnabled()
    window._table.selectRow(1)
    assert not window._block_button.isEnabled() and window._allow_button.isEnabled() and window._forget_button.isEnabled()


def test_block_and_allow_send_orders_for_the_selected_device(window) -> None:
    window._on_service(service_status([device(A)]))
    orders: list[dict] = []
    window.requestServiceAction.connect(orders.append)
    window._table.selectRow(0)
    window._block_button.click()
    assert orders == [{"action": "block", "mac": A}]


def test_temporary_access_sends_the_chosen_duration(window, monkeypatch) -> None:
    window._on_service(service_status([device(A)]))
    orders: list[dict] = []
    window.requestServiceAction.connect(orders.append)
    monkeypatch.setattr(DurationDialog, "exec", lambda self: 1)
    monkeypatch.setattr(DurationDialog, "seconds", lambda self: 12 * 3600)
    window._table.selectRow(0)
    window._temporary_button.click()
    assert orders == [{"action": "allow_for", "mac": A, "seconds": 43200}]


def test_strict_checkbox_sends_the_mode_but_display_updates_do_not(window) -> None:
    orders: list[dict] = []
    window.requestServiceAction.connect(orders.append)
    window._on_service(service_status(default_mode="strict"))
    assert window._strict.isChecked() and orders == []  # un simple affichage ne renvoie aucun ordre
    window._strict.setChecked(False)
    assert orders == [{"action": "set_default_mode", "mode": "open"}]


def test_renaming_a_device_is_saved_and_wins_over_the_host_name(window) -> None:
    window._on_state(state_with(Client(A, ("Redmi-13C",), "192.168.137.41")))
    window._table.item(0, 0).setText("Téléphone de Marie")
    assert Settings.load().device_name(A) == "Téléphone de Marie"
    window._on_state(state_with(Client(A, ("Redmi-13C",), "192.168.137.41")))
    assert cells(window)[0] == "Téléphone de Marie"


def test_refreshing_does_not_write_settings(window) -> None:
    window._on_state(state_with(Client(A, ("Redmi-13C",), None)))
    assert not settings_file().exists()


def test_selection_survives_a_refresh(window) -> None:
    window._on_service(service_status([device(A), device(B)]))
    window._table.selectRow(1)
    window._on_service(service_status([device(A), device(B)]))
    assert window._selected_mac() == B


def test_first_events_are_not_announced_but_later_ones_are(window) -> None:
    old = [{"seq": 1, "kind": "new_device", "mac": A, "detail": ""}]
    window._on_service(service_status(events=old))
    assert window.statusBar().currentMessage() != "Nouvel appareil connecté : Redmi-13C"  # ancien : on ne l'annonce pas
    window._on_service(service_status(events=old + [{"seq": 2, "kind": "access_expired", "mac": A, "detail": ""}]))
    assert window.statusBar().currentMessage() == "Accès expiré : Redmi-13C est maintenant bloqué"


def test_service_restart_resets_the_event_counter_without_replaying(window) -> None:
    window._on_service(service_status(events=[{"seq": 40, "kind": "new_device", "mac": A, "detail": ""}]))
    window._on_service(service_status(events=[{"seq": 1, "kind": "new_device", "mac": A, "detail": ""}]))  # redémarré
    window._on_service(service_status(events=[{"seq": 1, "kind": "new_device", "mac": A, "detail": ""}, {"seq": 2, "kind": "pass_expired", "mac": "", "detail": "x"}]))
    assert window.statusBar().currentMessage() == "Un pass invité a expiré sans être utilisé"


def test_qr_button_only_when_the_password_is_known(window) -> None:
    window._render()
    assert window._qr_button.isHidden()
    window._settings.set_wifi_password(PASSPHRASE)
    window._render()
    assert not window._qr_button.isHidden()


def test_credentials_applied_stores_the_password_and_shows_the_qr(window, monkeypatch) -> None:
    shown: list[tuple] = []
    monkeypatch.setattr(ShareDialog, "exec", lambda self: shown.append(self.windowTitle()) or 0)
    window._on_credentials_applied("JEMK_PC", PASSPHRASE)
    assert window._settings.wifi_password() == PASSPHRASE and shown == ["Connecter un appareil"]


def test_toggle_asks_before_stopping_and_starts_directly(window, monkeypatch) -> None:
    requests: list[str] = []
    window.requestStart.connect(lambda: requests.append("start"))
    window.requestStop.connect(lambda: requests.append("stop"))
    window._on_state(state_with(on=False))
    window._toggle.click()
    assert requests == ["start"]
    window._on_state(state_with(on=True))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    window._toggle.click()
    assert requests == ["start"]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    window._toggle.click()
    assert requests == ["start", "stop"]


def test_unavailable_hotspot_disables_actions_and_says_why(window) -> None:
    window._on_state(HotspotState(available=False, reason="Aucune connexion Internet à partager"))
    assert "indisponible" in window._status.text() and "Internet" in window._details.text()
    assert not window._toggle.isEnabled() and not window._password_button.isEnabled()


def test_auto_restart_checkbox_is_saved_and_forwarded(window) -> None:
    forwarded: list[bool] = []
    window.requestAutoRestart.connect(forwarded.append)
    window._auto_restart.setChecked(True)
    assert forwarded == [True] and Settings.load().auto_restart is True


# --- pass invité ---------------------------------------------------------------------------------


def test_new_pass_flow_asks_strict_mode_then_duration_then_creates(window, monkeypatch) -> None:
    window._settings.set_wifi_password(PASSPHRASE)
    window._on_state(state_with(Client(A, ("Redmi-13C",), None)))
    window._on_service(service_status(default_mode="open"))
    orders: list[dict] = []
    window.requestServiceAction.connect(orders.append)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(DurationDialog, "exec", lambda self: 1)
    monkeypatch.setattr(DurationDialog, "seconds", lambda self: 24 * 3600)
    window._pass_button.click()
    assert orders == [{"action": "set_default_mode", "mode": "strict"}, {"action": "create_pass", "seconds": 86400}]


def test_new_pass_is_cancelled_if_strict_mode_is_refused(window, monkeypatch) -> None:
    window._settings.set_wifi_password(PASSPHRASE)
    window._on_state(state_with(Client(A, ("Redmi-13C",), None)))
    window._on_service(service_status(default_mode="open"))
    orders: list[dict] = []
    window.requestServiceAction.connect(orders.append)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    window._pass_button.click()
    assert orders == []


def test_pass_dialog_tracks_claim_expiry_and_revocation(qapp) -> None:
    guest = {"id": "ab12", "duration": 43200, "claim_deadline": time.time() + 1800}
    dialog = PassDialog(None, "JEMK_PC", PASSPHRASE, guest)
    assert "valable encore" in dialog._state.text() and dialog._revoke.isEnabled()
    revoked: list[str] = []
    dialog.revokeRequested.connect(revoked.append)
    dialog._revoke.click()
    assert revoked == ["ab12"]

    dialog.update_from_service(service_status(passes=[{"id": "ab12", "duration": 43200, "created_at": 0, "claim_deadline": 0}]))
    assert not dialog._finished  # le pass attend toujours
    claimed = service_status(events=[{"seq": 3, "kind": "pass_claimed", "mac": B, "detail": "ab12"}], devices=[device(B, names=["Invité"])])
    dialog.update_from_service(claimed)
    assert dialog._finished and "Pass utilisé par Invité" in dialog._state.text() and not dialog._revoke.isEnabled()


def test_pass_dialog_reports_an_unused_pass_gone(qapp) -> None:
    dialog = PassDialog(None, "JEMK_PC", PASSPHRASE, {"id": "zz", "duration": 21600, "claim_deadline": time.time() + 60})
    dialog.update_from_service(service_status())
    assert dialog._finished and "n'est plus disponible" in dialog._state.text()


def test_pass_dialog_expires_when_the_window_closes(qapp) -> None:
    dialog = PassDialog(None, "JEMK_PC", PASSPHRASE, {"id": "zz", "duration": 21600, "claim_deadline": time.time() - 1})
    assert dialog._finished and "expiré" in dialog._state.text()


# --- dialogues ----------------------------------------------------------------------------------


def test_duration_dialog_presets_and_custom(qapp) -> None:
    dialog = DurationDialog(None, "Titre", "Intro")
    assert dialog.seconds() == 6 * 3600
    dialog._presets[24].setChecked(True)
    assert dialog.seconds() == 24 * 3600
    assert not dialog._custom_hours.isEnabled()  # le champ libre est grisé tant qu'« Autre durée » n'est pas choisi
    dialog._custom_radio.setChecked(True)
    assert dialog._custom_hours.isEnabled() and dialog.seconds() == 48 * 3600  # la valeur affichée par défaut suffit
    dialog._custom_hours.setValue(72)
    assert dialog.seconds() == 72 * 3600


def test_password_dialog_proposes_a_valid_password_and_refuses_a_bad_one(qapp) -> None:
    dialog = PasswordDialog(None, "JEMK_PC")
    ssid, passphrase = dialog.values()
    assert ssid == "JEMK_PC" and len(passphrase) == 14
    dialog._passphrase.setText("court")
    dialog._validate()
    assert "entre 8 et 63" in dialog._error.text() and dialog.result() != 1


def test_share_dialog_clears_the_clipboard_only_if_unchanged(qapp) -> None:
    dialog = ShareDialog(None, "JEMK_PC", PASSPHRASE)
    dialog._copy()
    assert QApplication.clipboard().text() == PASSPHRASE
    dialog._clear_clipboard()
    assert QApplication.clipboard().text() != PASSPHRASE
    dialog._copy()
    QApplication.clipboard().setText("autre chose")
    dialog._clear_clipboard()
    assert QApplication.clipboard().text() == "autre chose"
