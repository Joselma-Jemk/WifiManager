"""Interface Qt : hotspot, appareils, blocage, accès temporaires, pass invités avec QR code, données consommées."""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from wifimanager.controller import HotspotWorker
from wifimanager.dialogs import DurationDialog, PassDialog, PasswordDialog, ShareDialog
from wifimanager.formatting import access_text, duration_label, usage_text
from wifimanager.models import HotspotState
from wifimanager.neighbors import run_arp
from wifimanager.passwords import validate_passphrase
from wifimanager.psession import PowerShellSession
from wifimanager.settings import Settings
from wifimanager.usage import format_bytes
from wifimanager.watchdog import Watchdog, WatchdogConfig

COLORS = {"on": "#1b9e4b", "off": "#8a8f98", "warn": "#d98e04", "bad": "#c0392b"}
NEEDS_SERVICE = "Active d'abord le blocage (service administrateur)"
HEADERS = ["Appareil", "Adresse IP", "Adresse MAC", "Accès", "Données", "Statut"]

__all__ = ["MainWindow", "PasswordDialog", "PassDialog", "ShareDialog", "main"]


class MainWindow(QMainWindow):
    requestPoll = Signal()
    requestStart = Signal()
    requestStop = Signal()
    requestCredentials = Signal(str, str)
    requestAutoRestart = Signal(bool)
    requestServiceAction = Signal(object)
    requestInstall = Signal()
    requestUninstall = Signal()
    serviceUpdated = Signal(object)  # relayé aux dialogues ouverts (pass invité)

    def __init__(self, settings: Settings, worker: HotspotWorker) -> None:
        super().__init__()
        self.setWindowTitle("WifiManager")
        self.resize(980, 720)
        self._settings = settings
        self._state: HotspotState | None = None
        self._service: dict[str, Any] | None = None
        self._rows: list[dict[str, Any]] = []
        self._last_event = 0
        self._events_primed = False
        self._pass_password: str | None = None

        self._build_header()
        self._build_service_bar()
        self._build_devices()
        self._build_usage()

        self._auto_restart = QCheckBox("Relancer automatiquement le hotspot s'il s'éteint")
        self._auto_restart.setChecked(settings.auto_restart)
        self._auto_restart.setToolTip(
            "Si tu éteins le hotspot depuis Windows, il sera relancé. Un arrêt fait avec le bouton de cette fenêtre "
            "n'est jamais défait."
        )
        self._auto_restart.toggled.connect(self._on_auto_restart)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self._card)
        layout.addWidget(self._service_card)
        layout.addWidget(self._devices_box, stretch=1)
        layout.addWidget(self._usage_card)
        layout.addWidget(self._auto_restart)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Prêt")

        self._connect_worker(worker)
        self._render()

    # --- construction -------------------------------------------------------------------------

    def _build_header(self) -> None:
        self._dot = QLabel("●")
        self._status = QLabel("Lecture en cours…")
        self._status.setStyleSheet("font-size: 16px; font-weight: 600;")
        self._details = QLabel("")
        self._details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._toggle = QPushButton("Démarrer le hotspot")
        self._toggle.clicked.connect(self._on_toggle)
        self._password_button = QPushButton("Changer le mot de passe…")
        self._password_button.clicked.connect(self._on_password)
        self._qr_button = QPushButton("QR code")
        self._qr_button.setToolTip("Affiche le QR code de connexion avec le mot de passe actuel")
        self._qr_button.clicked.connect(self._on_show_qr)
        self._refresh_button = QPushButton("Actualiser")
        self._refresh_button.clicked.connect(self.requestPoll)

        header = QHBoxLayout()
        header.addWidget(self._dot)
        header.addWidget(self._status)
        header.addStretch()
        for button in (self._refresh_button, self._toggle, self._password_button, self._qr_button):
            header.addWidget(button)

        self._card = QFrame()
        self._card.setFrameShape(QFrame.Shape.StyledPanel)
        card_layout = QVBoxLayout(self._card)
        card_layout.addLayout(header)
        card_layout.addWidget(self._details)

    def _build_service_bar(self) -> None:
        self._service_label = QLabel("")
        self._service_label.setWordWrap(True)
        self._install_button = QPushButton("Activer le blocage…")
        self._install_button.clicked.connect(self._on_install)
        self._uninstall_button = QPushButton("Désactiver le blocage")
        self._uninstall_button.clicked.connect(self._on_uninstall)
        self._pass_button = QPushButton("Pass invité…")
        self._pass_button.setToolTip("Crée un QR code d'accès limité dans le temps pour un invité")
        self._pass_button.clicked.connect(self._on_new_pass)
        self._strict = QCheckBox("Mode strict : retenir les nouveaux appareils jusqu'à mon accord")
        self._strict.setToolTip(
            "Un nouvel appareil est bloqué tant que tu ne l'as pas autorisé, sauf s'il se présente avec un pass invité."
        )
        self._strict.toggled.connect(self._on_strict)

        top = QHBoxLayout()
        top.addWidget(self._service_label, stretch=1)
        for button in (self._pass_button, self._install_button, self._uninstall_button):
            top.addWidget(button)
        self._service_card = QFrame()
        self._service_card.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self._service_card)
        layout.addLayout(top)
        layout.addWidget(self._strict)

    def _build_devices(self) -> None:
        self._table = QTableWidget(0, len(HEADERS))
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.itemSelectionChanged.connect(self._update_device_buttons)

        self._block_button = QPushButton("Bloquer")
        self._allow_button = QPushButton("Autoriser")
        self._temporary_button = QPushButton("Accès temporaire…")
        self._forget_button = QPushButton("Retirer la règle")
        self._block_button.clicked.connect(lambda: self._send_for_selected("block"))
        self._allow_button.clicked.connect(lambda: self._send_for_selected("allow"))
        self._temporary_button.clicked.connect(self._on_temporary)
        self._forget_button.clicked.connect(lambda: self._send_for_selected("forget"))

        buttons = QHBoxLayout()
        for button in (self._block_button, self._allow_button, self._temporary_button, self._forget_button):
            buttons.addWidget(button)
        buttons.addStretch()
        self._devices_box = QGroupBox("Appareils (double-clic sur un nom pour le changer)")
        layout = QVBoxLayout(self._devices_box)
        layout.addWidget(self._table)
        layout.addLayout(buttons)

    def _build_usage(self) -> None:
        self._usage_label = QLabel("")
        self._reset_usage = QPushButton("Remettre à zéro")
        self._reset_usage.clicked.connect(self._on_reset_usage)
        row = QHBoxLayout()
        row.addWidget(self._usage_label, stretch=1)
        row.addWidget(self._reset_usage)
        self._usage_card = QFrame()
        self._usage_card.setFrameShape(QFrame.Shape.StyledPanel)
        QVBoxLayout(self._usage_card).addLayout(row)

    def _connect_worker(self, worker: HotspotWorker) -> None:
        self.requestPoll.connect(worker.poll)
        self.requestStart.connect(worker.start_hotspot)
        self.requestStop.connect(worker.stop_hotspot)
        self.requestCredentials.connect(worker.change_credentials)
        self.requestAutoRestart.connect(worker.set_auto_restart)
        self.requestServiceAction.connect(worker.service_action)
        self.requestInstall.connect(worker.install_service)
        self.requestUninstall.connect(worker.uninstall_service)
        worker.stateChanged.connect(self._on_state)
        worker.serviceChanged.connect(self._on_service)
        worker.message.connect(self.statusBar().showMessage)
        worker.failed.connect(self._on_failure)
        worker.busyChanged.connect(self._on_busy)
        worker.credentialsApplied.connect(self._on_credentials_applied)
        worker.passCreated.connect(self._on_pass_created)

    # --- réception ------------------------------------------------------------------------------

    def _on_state(self, state: HotspotState) -> None:
        self._state = state
        self._render()
        if self._service is None:
            self.statusBar().showMessage(f"Dernière lecture : {datetime.now():%H:%M:%S}")

    def _on_service(self, service: dict[str, Any] | None) -> None:
        self._service = service
        self._announce_events(service)
        self._render()
        self.serviceUpdated.emit(service)

    def _announce_events(self, service: dict[str, Any] | None) -> None:
        """Annonce dans la barre d'état ce qui s'est passé depuis la dernière lecture (nouvel appareil, expiration…)."""
        if service is None:
            self._events_primed = False  # au retour du service, on repart de zéro sans annoncer l'ancien
            return
        events = service.get("events", [])
        newest = max((event["seq"] for event in events), default=0)
        if not self._events_primed or newest < self._last_event:
            # Première lecture, ou service redémarré (son compteur repart de zéro) : on prend le point de départ sans
            # annoncer ce qui s'est passé avant que la fenêtre regarde.
            self._last_event = newest
            self._events_primed = True
            return
        for event in events:
            if event["seq"] <= self._last_event:
                continue
            self._last_event = event["seq"]
            text = self._event_text(event, service)
            if text:
                self.statusBar().showMessage(text, 20_000)

    def _device_label(self, mac: str, service: dict[str, Any]) -> str:
        custom = self._settings.device_name(mac) if mac else None
        device = next((d for d in service.get("devices", []) if d["mac"] == mac), None)
        host = device["names"][0] if device and device.get("names") else None
        return custom or host or mac or "un appareil"

    def _event_text(self, event: dict[str, Any], service: dict[str, Any]) -> str:
        who = self._device_label(event.get("mac", ""), service)
        return {
            "new_device": f"Nouvel appareil connecté : {who}",
            "new_device_held": f"Nouvel appareil retenu, en attente de ton accord : {who}",
            "pass_claimed": f"Pass invité utilisé par {who}",
            "access_expired": f"Accès expiré : {who} est maintenant bloqué",
            "pass_expired": "Un pass invité a expiré sans être utilisé",
        }.get(event.get("kind", ""), "")

    def _on_failure(self, text: str) -> None:
        self.statusBar().showMessage(text, 15_000)
        self._dot.setStyleSheet(f"color: {COLORS['warn']}; font-size: 20px;")

    def _on_busy(self, busy: bool) -> None:
        for button in (self._toggle, self._password_button, self._refresh_button, self._install_button, self._uninstall_button):
            button.setEnabled(not busy)
        if not busy:
            self._render()

    def _on_credentials_applied(self, ssid: str, passphrase: str) -> None:
        try:
            self._settings.set_wifi_password(passphrase)  # protégé par DPAPI : pour réafficher le QR code plus tard
            self._settings.save()
        except Exception:  # noqa: BLE001 - ne pas perdre l'affichage du QR si la protection échoue
            pass
        self._render()
        ShareDialog(self, ssid, passphrase).exec()

    # --- affichage ------------------------------------------------------------------------------

    def _render(self) -> None:
        self._render_header()
        self._render_service()
        self._rows = self._build_rows()
        self._fill_table()
        self._render_usage()
        self._update_device_buttons()

    def _render_header(self) -> None:
        state = self._state
        self._qr_button.setVisible(self._settings.wifi_password() is not None)
        if state is None:
            self._paint("off", "Lecture en cours…", "")
            self._toggle.setEnabled(False)
            self._password_button.setEnabled(False)
        elif not state.available:
            self._paint("warn", "Hotspot indisponible", state.reason)
            self._toggle.setEnabled(False)
            self._password_button.setEnabled(False)
        else:
            kind, title = ("on", "Hotspot allumé") if state.is_on else ("off", "Hotspot éteint")
            parts = [f"Réseau : {state.ssid}", f"Bande : {state.band}", f"Appareils : {state.client_count}/{state.max_clients}"]
            self._paint(kind, title, "   |   ".join(parts))
            self._toggle.setText("Arrêter le hotspot" if state.is_on else "Démarrer le hotspot")
            self._toggle.setEnabled(True)
            self._password_button.setEnabled(True)

    def _paint(self, kind: str, title: str, details: str) -> None:
        self._dot.setStyleSheet(f"color: {COLORS[kind]}; font-size: 20px;")
        self._status.setText(title)
        self._details.setText(details)

    def _render_service(self) -> None:
        active = self._service is not None
        self._install_button.setVisible(not active)
        self._uninstall_button.setVisible(active)
        self._pass_button.setEnabled(active)
        self._strict.setEnabled(active)
        if active:
            error = self._service["service"].get("error")
            text = "Blocage actif : le service applique les règles, même fenêtre fermée."
            if error:
                text += f"   ⚠ {error}"
            self._service_label.setText(text)
            self._strict.blockSignals(True)  # l'affichage ne doit pas passer pour un clic de l'utilisateur
            self._strict.setChecked(self._service.get("default_mode") == "strict")
            self._strict.blockSignals(False)
        else:
            self._service_label.setText(
                "Blocage désactivé : active-le pour bloquer des appareils, donner des accès de 6/12/24 h et mesurer les données."
            )
            self._strict.blockSignals(True)
            self._strict.setChecked(False)
            self._strict.blockSignals(False)

    def _build_rows(self) -> list[dict[str, Any]]:
        """Lignes du tableau : le service connaît aussi les appareils partis ; sans lui, seulement ceux qui sont connectés."""
        if self._service is not None:
            return list(self._service["devices"])
        clients = self._state.clients if self._state is not None and self._state.available else ()
        return [
            {"mac": c.mac, "names": list(c.names), "ip": c.ip, "connected": True, "mode": None, "blocked": False}
            for c in clients
        ]

    def _fill_table(self) -> None:
        selected = self._selected_mac()
        self._table.blockSignals(True)  # le remplissage ne doit pas passer pour un renommage de l'utilisateur
        self._table.setRowCount(len(self._rows))
        for row, device in enumerate(self._rows):
            mac = device["mac"]
            name = QTableWidgetItem(
                self._settings.device_name(mac) or (device["names"][0] if device.get("names") else mac)
            )
            name.setData(Qt.ItemDataRole.UserRole, mac)
            name.setFlags(name.flags() | Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, name)
            access = access_text(device) if device.get("mode") else "—"
            data = usage_text(device) if self._service is not None else "—"
            status = "Connecté" if device.get("connected") else "Absent"
            for column, text in enumerate((device.get("ip") or "—", mac, access, data, status), start=1):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, column, item)
            self._tint_row(row, device)
        self._table.blockSignals(False)
        if selected is not None:
            for row, device in enumerate(self._rows):
                if device["mac"] == selected:
                    self._table.selectRow(row)

    def _tint_row(self, row: int, device: dict[str, Any]) -> None:
        mode = device.get("mode")
        color = {"blocked": COLORS["bad"], "pending": COLORS["warn"]}.get(mode or "")
        if color is None and not device.get("connected"):
            color = COLORS["off"]
        if color:
            for column in range(self._table.columnCount()):
                item = self._table.item(row, column)
                if item is not None:
                    item.setForeground(QBrush(QColor(color)))

    def _render_usage(self) -> None:
        self._reset_usage.setEnabled(self._service is not None)
        if self._service is None:
            self._usage_label.setText("Données consommées : disponibles quand le blocage est actif.")
            return
        usage = self._service["usage"]
        since = datetime.fromtimestamp(usage["since"]).strftime("%d/%m %H:%M")
        text = (
            f"Données du hotspot depuis le {since} : ↓ {format_bytes(usage['downloaded'])}   ↑ {format_bytes(usage['uploaded'])}"
            f"   (total {format_bytes(usage['downloaded'] + usage['uploaded'])})"
        )
        if usage.get("unattributed"):
            text += f"   dont {format_bytes(usage['unattributed'])} non attribués"
        self._usage_label.setText(text + "\nLe total est exact. Par appareil : exact s'il est seul, estimé s'ils sont plusieurs.")

    def _update_device_buttons(self) -> None:
        device = self._selected_device()
        active = self._service is not None and device is not None
        mode = device.get("mode") if device else None
        self._block_button.setEnabled(active and mode not in ("blocked",))
        self._allow_button.setEnabled(active and mode in ("blocked", "pending", "temporary"))
        self._temporary_button.setEnabled(active)
        self._forget_button.setEnabled(active and device is not None and device.get("explicit") is True)
        tooltip = "" if self._service is not None else NEEDS_SERVICE
        for button in (self._block_button, self._allow_button, self._temporary_button, self._forget_button):
            button.setToolTip(tooltip)

    # --- sélection ---------------------------------------------------------------------------------

    def _selected_mac(self) -> str | None:
        row = self._table.currentRow()
        item = self._table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _selected_device(self) -> dict[str, Any] | None:
        mac = self._selected_mac()
        return next((d for d in self._rows if d["mac"] == mac), None)

    # --- actions de l'utilisateur ------------------------------------------------------------------------

    def _on_toggle(self) -> None:
        if self._state is not None and self._state.is_on:
            answer = QMessageBox.question(self, "WifiManager", "Arrêter le hotspot ? Tous les appareils seront déconnectés.")
            if answer == QMessageBox.StandardButton.Yes:
                self.requestStop.emit()
        else:
            self.requestStart.emit()

    def _on_password(self) -> None:
        ssid = self._state.ssid if self._state is not None else ""
        dialog = PasswordDialog(self, ssid)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.requestCredentials.emit(*dialog.values())

    def _on_show_qr(self) -> None:
        password = self._settings.wifi_password()
        if password is not None and self._state is not None:
            ShareDialog(self, self._state.ssid, password).exec()

    def _on_auto_restart(self, enabled: bool) -> None:
        self._settings.auto_restart = enabled
        self._settings.save()
        self.requestAutoRestart.emit(enabled)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        mac = item.data(Qt.ItemDataRole.UserRole)
        if mac:
            self._settings.rename_device(mac, item.text())
            self._settings.save()

    def _send_for_selected(self, action: str) -> None:
        mac = self._selected_mac()
        if mac is not None:
            self.requestServiceAction.emit({"action": action, "mac": mac})

    def _on_temporary(self) -> None:
        mac = self._selected_mac()
        if mac is None:
            return
        who = self._settings.device_name(mac) or mac
        dialog = DurationDialog(
            self,
            "Accès temporaire",
            f"{who} sera autorisé pendant la durée choisie, puis bloqué automatiquement.",
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.requestServiceAction.emit({"action": "allow_for", "mac": mac, "seconds": dialog.seconds()})

    def _on_strict(self, enabled: bool) -> None:
        self.requestServiceAction.emit({"action": "set_default_mode", "mode": "strict" if enabled else "open"})

    def _on_reset_usage(self) -> None:
        answer = QMessageBox.question(self, "WifiManager", "Remettre à zéro les compteurs de données ?")
        if answer == QMessageBox.StandardButton.Yes:
            self.requestServiceAction.emit({"action": "reset_usage"})

    def _on_install(self) -> None:
        answer = QMessageBox.question(
            self,
            "Activer le blocage",
            "Pour bloquer des appareils, WifiManager installe un petit service qui tourne avec les droits "
            "administrateur et démarre à chaque ouverture de session. Windows va te demander ton autorisation "
            "une seule fois.\n\nTu peux le désactiver à tout moment, ce qui retire tous les blocages.\n\nContinuer ?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.requestInstall.emit()

    def _on_uninstall(self) -> None:
        answer = QMessageBox.question(
            self,
            "Désactiver le blocage",
            "Le service sera arrêté et supprimé : plus aucun appareil ne sera bloqué et les accès temporaires "
            "ne seront plus appliqués. Continuer ?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.requestUninstall.emit()

    # --- pass invité ----------------------------------------------------------------------------------

    def _ask_current_password(self) -> str | None:
        """Le QR code exige le mot de passe actuel, que Windows ne permet pas de relire : on le demande une fois."""
        known = self._settings.wifi_password()
        if known is not None:
            return known
        text, ok = QInputDialog.getText(
            self,
            "Mot de passe du hotspot",
            "Pour afficher un QR code, WifiManager doit connaître le mot de passe actuel du hotspot\n"
            "(Windows ne permet pas de le relire). Il sera conservé chiffré pour ton compte Windows seulement.",
            QLineEdit.EchoMode.Password,
        )
        if not ok:
            return None
        try:
            validate_passphrase(text)
        except ValueError as exc:
            QMessageBox.warning(self, "WifiManager", str(exc))
            return None
        self._settings.set_wifi_password(text)
        self._settings.save()
        self._render()
        return text

    def _on_new_pass(self) -> None:
        if self._service is None or self._state is None:
            return
        password = self._ask_current_password()
        if password is None:
            return
        if self._service.get("default_mode") != "strict":
            answer = QMessageBox.question(
                self,
                "Mode strict",
                "Pour qu'un pass limite vraiment l'accès, les nouveaux appareils doivent être retenus jusqu'à ton accord "
                "(mode strict). Les appareils déjà connus ne sont pas coupés.\n\nActiver le mode strict maintenant ?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.requestServiceAction.emit({"action": "set_default_mode", "mode": "strict"})
        dialog = DurationDialog(
            self,
            "Pass invité",
            "Durée de l'accès accordé à l'invité, à partir de sa connexion. Le pass peut être utilisé dans les 30 "
            "prochaines minutes, par un appareil encore inconnu.",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._pass_password = password
        self.requestServiceAction.emit({"action": "create_pass", "seconds": dialog.seconds()})

    def _on_pass_created(self, guest: dict[str, Any]) -> None:
        password, self._pass_password = self._pass_password, None
        if password is None or self._state is None:
            return
        dialog = PassDialog(self, self._state.ssid, password, guest)
        dialog.revokeRequested.connect(lambda pass_id: self.requestServiceAction.emit({"action": "revoke_pass", "id": pass_id}))
        self.serviceUpdated.connect(dialog.update_from_service)
        self.statusBar().showMessage(f"Pass invité de {duration_label(guest['duration'])} créé", 10_000)
        dialog.exec()
        self.serviceUpdated.disconnect(dialog.update_from_service)


# --- démarrage ---------------------------------------------------------------------------------


def start_worker(worker: QObject) -> QThread:
    """Place le travailleur dans son propre fil et le démarre."""
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.start_polling)  # type: ignore[attr-defined]
    thread.start()
    return thread


def main() -> int:
    if "--service" in sys.argv:  # l'exécutable installé sert aussi de service de blocage
        from wifimanager.service import main as service_main

        return service_main()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("WifiManager")
    settings = Settings.load()
    session = PowerShellSession()
    watchdog = Watchdog(WatchdogConfig(enabled=settings.auto_restart))
    worker = HotspotWorker(session, run_arp, watchdog, settings.poll_seconds)
    window = MainWindow(settings, worker)
    thread = start_worker(worker)

    def shutdown() -> None:
        worker.shutdown()
        thread.quit()
        thread.wait(3000)
        session.close()

    app.aboutToQuit.connect(shutdown)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
