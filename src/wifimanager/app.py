"""Interface Qt : état du hotspot, appareils, mot de passe en un clic avec QR code, relance automatique."""

from __future__ import annotations

import sys
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
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
from wifimanager.models import HotspotState
from wifimanager.neighbors import run_arp
from wifimanager.passwords import (
    generate_passphrase,
    validate_passphrase,
    validate_ssid,
)
from wifimanager.psession import PowerShellSession
from wifimanager.qr import render_qr, wifi_payload
from wifimanager.settings import Settings
from wifimanager.watchdog import Watchdog, WatchdogConfig

CLIPBOARD_CLEAR_MS = 30_000
BLOCK_UNAVAILABLE = "Disponible avec le service administrateur (étape suivante)"

COLORS = {"on": "#1b9e4b", "off": "#8a8f98", "warn": "#d98e04"}


# --- dialogues ----------------------------------------------------------------------


class PasswordDialog(QDialog):
    """Saisie du nouveau nom et du nouveau mot de passe, avec un mot de passe lisible déjà proposé."""

    def __init__(self, parent: QWidget | None, ssid: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Changer le mot de passe du hotspot")
        self._ssid = QLineEdit(ssid)
        self._passphrase = QLineEdit(generate_passphrase())
        generate = QPushButton("Générer un autre")
        generate.clicked.connect(lambda: self._passphrase.setText(generate_passphrase()))
        self._error = QLabel()
        self._error.setStyleSheet("color: #c0392b;")
        self._error.setWordWrap(True)

        passphrase_row = QHBoxLayout()
        passphrase_row.addWidget(self._passphrase)
        passphrase_row.addWidget(generate)
        form = QFormLayout()
        form.addRow("Nom du réseau :", self._ssid)
        form.addRow("Nouveau mot de passe :", passphrase_row)

        warning = QLabel(
            "Tous les appareils connectés seront déconnectés et devront se reconnecter avec le nouveau mot de passe."
        )
        warning.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Appliquer")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(warning)
        layout.addWidget(self._error)
        layout.addWidget(buttons)
        self.setMinimumWidth(460)

    def _validate(self) -> None:
        try:
            validate_ssid(self._ssid.text())
            validate_passphrase(self._passphrase.text())
        except ValueError as exc:
            self._error.setText(str(exc))
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return self._ssid.text(), self._passphrase.text()


class ShareDialog(QDialog):
    """QR code et mot de passe, à montrer juste après un changement : le mot de passe actuel ne peut pas être relu."""

    def __init__(self, parent: QWidget | None, ssid: str, passphrase: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connecter un appareil")
        self._passphrase = passphrase
        self._copied: str | None = None

        qr = QLabel()
        qr.setPixmap(render_qr(wifi_payload(ssid, passphrase)))
        qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info = QLabel(f"<b>{_escape(ssid)}</b><br>Mot de passe : <b>{_escape(passphrase)}</b>")
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel(
            "Scanne le QR code avec l'appareil photo du téléphone pour rejoindre le réseau. "
            "Note le mot de passe : il ne pourra plus être affiché ensuite."
        )
        hint.setWordWrap(True)
        copy = QPushButton("Copier le mot de passe")
        copy.clicked.connect(self._copy)
        close = QPushButton("Fermer")
        close.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addWidget(copy)
        buttons.addStretch()
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(qr)
        layout.addWidget(info)
        layout.addWidget(hint)
        layout.addLayout(buttons)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._passphrase)
        self._copied = self._passphrase
        QTimer.singleShot(CLIPBOARD_CLEAR_MS, self._clear_clipboard)

    def _clear_clipboard(self) -> None:
        """Vide le presse-papiers 30 s après la copie, seulement s'il contient encore le mot de passe."""
        if self._copied is not None and QApplication.clipboard().text() == self._copied:
            QApplication.clipboard().clear()
        self._copied = None


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --- fenêtre principale -------------------------------------------------------------------


class MainWindow(QMainWindow):
    requestPoll = Signal()
    requestStart = Signal()
    requestStop = Signal()
    requestCredentials = Signal(str, str)
    requestAutoRestart = Signal(bool)

    def __init__(self, settings: Settings, worker: HotspotWorker) -> None:
        super().__init__()
        self.setWindowTitle("WifiManager")
        self.resize(780, 560)
        self._settings = settings
        self._state: HotspotState | None = None

        self._dot = QLabel("●")
        self._status = QLabel("Lecture en cours…")
        self._status.setStyleSheet("font-size: 16px; font-weight: 600;")
        self._details = QLabel("")
        self._details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._toggle = QPushButton("Démarrer le hotspot")
        self._toggle.clicked.connect(self._on_toggle)
        self._password_button = QPushButton("Changer le mot de passe…")
        self._password_button.clicked.connect(self._on_password)
        self._refresh_button = QPushButton("Actualiser")
        self._refresh_button.clicked.connect(self.requestPoll)

        header = QHBoxLayout()
        header.addWidget(self._dot)
        header.addWidget(self._status)
        header.addStretch()
        header.addWidget(self._refresh_button)
        header.addWidget(self._toggle)
        header.addWidget(self._password_button)

        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card_layout = QVBoxLayout(card)
        card_layout.addLayout(header)
        card_layout.addWidget(self._details)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Appareil", "Nom d'hôte", "Adresse IP", "Adresse MAC", "Statut"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.itemChanged.connect(self._on_item_changed)
        block = QPushButton("Bloquer")
        temporary = QPushButton("Accès temporaire…")
        for button in (block, temporary):
            button.setEnabled(False)
            button.setToolTip(BLOCK_UNAVAILABLE)
        device_buttons = QHBoxLayout()
        device_buttons.addWidget(block)
        device_buttons.addWidget(temporary)
        device_buttons.addStretch()
        devices = QGroupBox("Appareils connectés (double-clic sur un nom pour le changer)")
        devices_layout = QVBoxLayout(devices)
        devices_layout.addWidget(self._table)
        devices_layout.addLayout(device_buttons)

        self._auto_restart = QCheckBox("Relancer automatiquement le hotspot s'il s'éteint")
        self._auto_restart.setChecked(settings.auto_restart)
        self._auto_restart.setToolTip(
            "Si tu éteins le hotspot depuis Windows, il sera relancé. Un arrêt fait avec le bouton de cette fenêtre "
            "n'est jamais défait."
        )
        self._auto_restart.toggled.connect(self._on_auto_restart)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(card)
        layout.addWidget(devices, stretch=1)
        layout.addWidget(self._auto_restart)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Prêt")

        self._connect_worker(worker)
        self._render(None)

    def _connect_worker(self, worker: HotspotWorker) -> None:
        self.requestPoll.connect(worker.poll)
        self.requestStart.connect(worker.start_hotspot)
        self.requestStop.connect(worker.stop_hotspot)
        self.requestCredentials.connect(worker.change_credentials)
        self.requestAutoRestart.connect(worker.set_auto_restart)
        worker.stateChanged.connect(self._on_state)
        worker.message.connect(self.statusBar().showMessage)
        worker.failed.connect(self._on_failure)
        worker.busyChanged.connect(self._on_busy)
        worker.credentialsApplied.connect(self._on_credentials_applied)

    # --- réception des informations du travailleur ---------------------------------------------

    def _on_state(self, state: HotspotState) -> None:
        self._state = state
        self._render(state)
        self.statusBar().showMessage(f"Dernière lecture : {datetime.now():%H:%M:%S}")

    def _on_failure(self, text: str) -> None:
        self.statusBar().showMessage(text, 15_000)
        self._dot.setStyleSheet(f"color: {COLORS['warn']}; font-size: 20px;")

    def _on_busy(self, busy: bool) -> None:
        for button in (self._toggle, self._password_button, self._refresh_button):
            button.setEnabled(not busy)
        if not busy:
            self._render(self._state)

    def _on_credentials_applied(self, ssid: str, passphrase: str) -> None:
        ShareDialog(self, ssid, passphrase).exec()

    # --- affichage ----------------------------------------------------------------------------

    def _render(self, state: HotspotState | None) -> None:
        if state is None:
            self._paint("off", "Lecture en cours…", "")
            self._toggle.setEnabled(False)
            self._password_button.setEnabled(False)
            self._fill_devices(())
            return
        if not state.available:
            self._paint("warn", "Hotspot indisponible", state.reason)
            self._toggle.setEnabled(False)
            self._password_button.setEnabled(False)
            self._fill_devices(())
            return
        if state.is_on:
            self._paint("on", "Hotspot allumé", self._details_text(state))
            self._toggle.setText("Arrêter le hotspot")
        else:
            self._paint("off", "Hotspot éteint", self._details_text(state))
            self._toggle.setText("Démarrer le hotspot")
        self._toggle.setEnabled(True)
        self._password_button.setEnabled(True)
        self._fill_devices(state.clients)

    def _paint(self, kind: str, title: str, details: str) -> None:
        self._dot.setStyleSheet(f"color: {COLORS[kind]}; font-size: 20px;")
        self._status.setText(title)
        self._details.setText(details)

    @staticmethod
    def _details_text(state: HotspotState) -> str:
        parts = [f"Réseau : {state.ssid}", f"Bande : {state.band}", f"Appareils : {state.client_count}/{state.max_clients}"]
        return "   |   ".join(parts)

    def _fill_devices(self, clients) -> None:
        self._table.blockSignals(True)  # le remplissage ne doit pas passer pour un renommage de l'utilisateur
        self._table.setRowCount(len(clients))
        for row, client in enumerate(clients):
            name = QTableWidgetItem(self._settings.device_name(client.mac) or client.label)
            name.setData(Qt.ItemDataRole.UserRole, client.mac)
            name.setFlags(name.flags() | Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, name)
            for column, text in enumerate(
                (client.names[0] if client.names else "—", client.ip or "—", client.mac, "Connecté"), start=1
            ):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, column, item)
        self._table.blockSignals(False)

    # --- actions de l'utilisateur ----------------------------------------------------------------

    def _on_toggle(self) -> None:
        if self._state is not None and self._state.is_on:
            answer = QMessageBox.question(
                self, "WifiManager", "Arrêter le hotspot ? Tous les appareils seront déconnectés."
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.requestStop.emit()
        else:
            self.requestStart.emit()

    def _on_password(self) -> None:
        ssid = self._state.ssid if self._state is not None else ""
        dialog = PasswordDialog(self, ssid)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.requestCredentials.emit(*dialog.values())

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


# --- démarrage ---------------------------------------------------------------------------------


def start_worker(worker: QObject) -> QThread:
    """Place le travailleur dans son propre fil et le démarre."""
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.start_polling)  # type: ignore[attr-defined]
    thread.start()
    return thread


def main() -> int:
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
