"""Interface Qt moderne : hotspot, appareils, blocage, accès temporaires, pass invités, données, maintien allumé."""

from __future__ import annotations

import sys
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from wifimanager import theme
from wifimanager.controller import HotspotWorker
from wifimanager.dialogs import DurationDialog, PassDialog, PasswordDialog, ShareDialog
from wifimanager.formatting import duration_label
from wifimanager.models import HotspotState
from wifimanager.neighbors import run_arp
from wifimanager.passwords import validate_passphrase
from wifimanager.psession import PowerShellSession
from wifimanager.settings import Settings
from wifimanager.usage import format_bytes
from wifimanager.watchdog import Watchdog, WatchdogConfig
from wifimanager.widgets import (
    Card,
    DeviceRow,
    Pill,
    StatTile,
    ToggleSwitch,
    muted,
    set_pill,
)

__all__ = ["MainWindow", "PasswordDialog", "PassDialog", "ShareDialog", "main"]

ICON_PATH = Path(__file__).parent / "assets" / "wifimanager.png"


def _logo_bytes() -> bytes | None:
    """Logo lu dans le paquet, y compris dans un exécutable compilé. None s'il est introuvable."""
    try:
        return resources.files("wifimanager").joinpath("assets").joinpath("wifimanager.png").read_bytes()
    except (OSError, ModuleNotFoundError, TypeError):
        pass
    return ICON_PATH.read_bytes() if ICON_PATH.exists() else None  # lancement depuis les sources


def app_icon() -> QIcon | None:
    """Icône de l'application, ou None si le logo est introuvable (Windows utilise alors l'icône du programme)."""
    data = _logo_bytes()
    if data is None:
        return None
    pixmap = QPixmap()
    return QIcon(pixmap) if pixmap.loadFromData(data) else None


KEEP_ALIVE_HELP = (
    "Relance le hotspot s'il s'éteint, et le service de blocage s'il s'arrête. Un arrêt fait avec l'interrupteur de "
    "cette fenêtre n'est jamais défait. Si Windows éteint le hotspot faute d'appareil connecté, désactive aussi "
    "« Économie d'énergie » dans Paramètres › Réseau › Point d'accès mobile."
)
STRICT_HELP = (
    "Un nouvel appareil est bloqué tant que tu ne l'as pas autorisé, sauf s'il se présente avec un pass invité. "
    "Les appareils déjà connus ne sont pas coupés."
)


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
        self.resize(940, 800)
        self.setMinimumSize(780, 640)
        self._settings = settings
        self._state: HotspotState | None = None
        self._service: dict[str, Any] | None = None
        self._rows: dict[str, DeviceRow] = {}
        self._last_event = 0
        self._events_primed = False
        self._pass_password: str | None = None

        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        layout.addLayout(self._build_title())
        layout.addWidget(self._build_hero())
        layout.addLayout(self._build_tiles())
        layout.addWidget(self._build_keep_alive())
        layout.addLayout(self._build_devices_header())
        self._devices_layout = QVBoxLayout()
        self._devices_layout.setSpacing(10)
        layout.addLayout(self._devices_layout)
        self._empty = muted(QLabel("Aucun appareil connecté pour le moment."))
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setContentsMargins(0, 24, 0, 24)
        layout.addWidget(self._empty)
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        self.setCentralWidget(scroll)
        self.statusBar().showMessage("Prêt")

        self._connect_worker(worker)
        self._render()

    # --- construction -------------------------------------------------------------------------

    def _build_title(self) -> QHBoxLayout:
        title = QLabel("WifiManager")
        title.setObjectName("title")
        subtitle = muted(QLabel("Pilote le hotspot de ce PC : appareils, accès et données."))
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(title)
        text.addWidget(subtitle)
        self._refresh_button = QToolButton()
        self._refresh_button.setText("⟳")
        self._refresh_button.setToolTip("Actualiser")
        self._refresh_button.clicked.connect(self.requestPoll)
        row = QHBoxLayout()
        row.addLayout(text, stretch=1)
        row.addWidget(self._refresh_button, alignment=Qt.AlignmentFlag.AlignTop)
        return row

    def _build_hero(self) -> Card:
        self._hero_pill = Pill()
        self._hero_ssid = QLabel("—")
        self._hero_ssid.setObjectName("ssid")
        self._hero_ssid.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._hero_details = muted(QLabel(""))
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self._hero_pill, alignment=Qt.AlignmentFlag.AlignLeft)
        left.addWidget(self._hero_ssid)
        left.addWidget(self._hero_details)

        self._hotspot_switch = ToggleSwitch()
        self._hotspot_switch.setAccessibleName("Hotspot allumé")
        self._hotspot_switch.clicked.connect(self._on_hotspot_clicked)
        self._hotspot_caption = QLabel("Hotspot")
        switch_row = QHBoxLayout()
        switch_row.setSpacing(10)
        switch_row.addStretch()
        switch_row.addWidget(self._hotspot_caption)
        switch_row.addWidget(self._hotspot_switch)

        self._password_button = QPushButton("Mot de passe…")
        self._password_button.clicked.connect(self._on_password)
        self._qr_button = QPushButton("QR code")
        self._qr_button.setToolTip("Affiche le QR code de connexion avec le mot de passe actuel")
        self._qr_button.clicked.connect(self._on_show_qr)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self._qr_button)
        buttons.addWidget(self._password_button)
        right = QVBoxLayout()
        right.setSpacing(12)
        right.addLayout(switch_row)
        right.addLayout(buttons)

        card = Card()
        row = QHBoxLayout(card)
        row.setContentsMargins(22, 20, 22, 20)
        row.addLayout(left, stretch=1)
        row.addLayout(right)
        return card

    def _build_tiles(self) -> QHBoxLayout:
        self._tile_devices = StatTile("Appareils")
        self._tile_data = StatTile("Données")
        self._tile_blocking = StatTile("Blocage")
        self._tile_blocking.button.clicked.connect(self._on_blocking_clicked)
        self._reset_usage = QPushButton("Remettre à zéro")
        self._reset_usage.setProperty("small", True)
        self._reset_usage.clicked.connect(self._on_reset_usage)
        self._tile_data.layout().insertWidget(self._tile_data.layout().count() - 1, self._reset_usage, alignment=Qt.AlignmentFlag.AlignLeft)
        row = QHBoxLayout()
        row.setSpacing(14)
        for tile in (self._tile_devices, self._tile_data, self._tile_blocking):
            row.addWidget(tile, stretch=1)
        return row

    def _build_keep_alive(self) -> Card:
        title = QLabel("Maintenir allumé")
        title.setObjectName("h2")
        help_text = muted(QLabel(KEEP_ALIVE_HELP))
        help_text.setWordWrap(True)
        self._keep_switch = ToggleSwitch()
        self._keep_switch.setAccessibleName("Maintenir allumé")
        self._keep_switch.set_checked_silently(self._settings.auto_restart)
        self._keep_switch.clicked.connect(self._on_keep_alive)
        text = QVBoxLayout()
        text.setSpacing(3)
        text.addWidget(title)
        text.addWidget(help_text)
        card = Card()
        row = QHBoxLayout(card)
        row.setContentsMargins(22, 16, 22, 16)
        row.setSpacing(18)
        row.addLayout(text, stretch=1)
        row.addWidget(self._keep_switch, alignment=Qt.AlignmentFlag.AlignVCenter)
        return card

    def _build_devices_header(self) -> QHBoxLayout:
        heading = QLabel("Appareils")
        heading.setObjectName("h2")
        strict_label = QLabel("Mode strict")
        strict_label.setToolTip(STRICT_HELP)
        self._strict_switch = ToggleSwitch()
        self._strict_switch.setToolTip(STRICT_HELP)
        self._strict_switch.setAccessibleName("Mode strict")
        self._strict_switch.clicked.connect(self._on_strict)
        self._pass_button = QPushButton("Pass invité…")
        self._pass_button.setProperty("primary", True)
        self._pass_button.setToolTip("Crée un QR code d'accès limité dans le temps pour un invité")
        self._pass_button.clicked.connect(self._on_new_pass)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(heading)
        row.addStretch()
        row.addWidget(strict_label)
        row.addWidget(self._strict_switch)
        row.addSpacing(10)
        row.addWidget(self._pass_button)
        return row

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
        if self._state is not None and self._state.available:
            set_pill(self._hero_pill, "Attention", "warning")

    def _on_busy(self, busy: bool) -> None:
        for widget in (self._hotspot_switch, self._password_button, self._refresh_button, self._tile_blocking.button):
            widget.setEnabled(not busy)
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
        self._render_hero()
        self._render_tiles()
        self._render_strict()
        self._render_devices()

    def _render_hero(self) -> None:
        state = self._state
        self._qr_button.setVisible(self._settings.wifi_password() is not None)
        if state is None:
            set_pill(self._hero_pill, "Lecture en cours…", "neutral")
            self._hero_ssid.setText("—")
            self._hero_details.setText("")
            self._hotspot_switch.set_checked_silently(False)
            self._hotspot_switch.setEnabled(False)
            self._password_button.setEnabled(False)
        elif not state.available:
            set_pill(self._hero_pill, "Indisponible", "warning")
            self._hero_ssid.setText("Hotspot indisponible")
            self._hero_details.setText(state.reason)
            self._hotspot_switch.set_checked_silently(False)
            self._hotspot_switch.setEnabled(False)
            self._password_button.setEnabled(False)
        else:
            on = state.is_on
            set_pill(self._hero_pill, "Allumé" if on else "Éteint", "success" if on else "neutral")
            self._hero_ssid.setText(state.ssid or "—")
            plural = "appareil" if state.client_count <= 1 else "appareils"
            self._hero_details.setText(f"Bande {state.band}  ·  {state.client_count} {plural} sur {state.max_clients}")
            self._hotspot_switch.set_checked_silently(on)
            self._hotspot_switch.setEnabled(True)
            self._password_button.setEnabled(True)

    def _render_tiles(self) -> None:
        state = self._state
        if state is not None and state.available:
            self._tile_devices.value.setText(f"{state.client_count}/{state.max_clients}")
            self._tile_devices.caption.setText("connecté" if state.client_count <= 1 else "connectés")
        else:
            self._tile_devices.value.setText("—")
            self._tile_devices.caption.setText("")

        service = self._service
        self._reset_usage.setVisible(service is not None)
        if service is None:
            self._tile_data.value.setText("—")
            self._tile_data.caption.setText("Mesure disponible quand le blocage est actif.")
            self._tile_blocking.value.setText("Inactif")
            self._tile_blocking.caption.setText("Active-le pour bloquer des appareils, limiter les accès dans le temps et mesurer les données.")
            self._tile_blocking.button.setText("Activer…")
            self._tile_blocking.button.setProperty("primary", True)
        else:
            usage = service["usage"]
            total = usage["downloaded"] + usage["uploaded"]
            since = datetime.fromtimestamp(usage["since"]).strftime("%d/%m %H:%M")
            self._tile_data.value.setText(format_bytes(total))
            caption = (
                f"{format_bytes(usage['downloaded'])} reçus  ·  {format_bytes(usage['uploaded'])} envoyés\n"
                f"depuis le {since}"
            )
            if usage.get("unattributed"):
                caption += f"\n{format_bytes(usage['unattributed'])} non attribués"
            self._tile_data.caption.setText(caption)
            error = service["service"].get("error")
            self._tile_blocking.value.setText("Actif")
            self._tile_blocking.caption.setText(
                (f"⚠ {error}" if error else "Les règles s'appliquent même fenêtre fermée.")
            )
            self._tile_blocking.button.setText("Désactiver")
            self._tile_blocking.button.setProperty("primary", False)
        self._tile_blocking.button.show()
        button = self._tile_blocking.button
        button.style().unpolish(button)
        button.style().polish(button)

    def _render_strict(self) -> None:
        active = self._service is not None
        self._strict_switch.setEnabled(active)
        self._pass_button.setEnabled(active)
        self._strict_switch.set_checked_silently(active and self._service.get("default_mode") == "strict")

    def _device_rows(self) -> list[dict[str, Any]]:
        """Appareils à montrer : le service connaît aussi ceux qui sont partis ; sans lui, seulement les connectés."""
        if self._service is not None:
            return list(self._service["devices"])
        clients = self._state.clients if self._state is not None and self._state.available else ()
        return [
            {"mac": c.mac, "names": list(c.names), "ip": c.ip, "connected": True, "mode": None, "blocked": False}
            for c in clients
        ]

    def _render_devices(self) -> None:
        devices = self._device_rows()
        wanted = [d["mac"] for d in devices]
        for mac in [m for m in self._rows if m not in wanted]:
            widget = self._rows.pop(mac)
            self._devices_layout.removeWidget(widget)
            widget.deleteLater()
        for index, device in enumerate(devices):
            mac = device["mac"]
            widget = self._rows.get(mac)
            if widget is None:
                widget = DeviceRow(mac)
                widget.actionRequested.connect(self._on_row_action)
                self._rows[mac] = widget
            widget.update_from(device, self._settings.device_name(mac), self._service is not None)
            if self._devices_layout.indexOf(widget) != index:
                self._devices_layout.removeWidget(widget)
                self._devices_layout.insertWidget(index, widget)
        self._empty.setVisible(not devices)

    # --- actions de l'utilisateur ------------------------------------------------------------------------

    def _on_hotspot_clicked(self, checked: bool) -> None:
        if checked:
            self.requestStart.emit()
            return
        answer = QMessageBox.question(self, "WifiManager", "Arrêter le hotspot ? Tous les appareils seront déconnectés.")
        if answer == QMessageBox.StandardButton.Yes:
            self.requestStop.emit()
        else:
            self._hotspot_switch.set_checked_silently(True)  # refusé : l'interrupteur revient sur « allumé »

    def _on_password(self) -> None:
        ssid = self._state.ssid if self._state is not None else ""
        dialog = PasswordDialog(self, ssid)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.requestCredentials.emit(*dialog.values())

    def _on_show_qr(self) -> None:
        password = self._settings.wifi_password()
        if password is not None and self._state is not None:
            ShareDialog(self, self._state.ssid, password).exec()

    def _on_keep_alive(self, enabled: bool) -> None:
        self._settings.auto_restart = enabled
        self._settings.save()
        self.requestAutoRestart.emit(enabled)

    def _on_row_action(self, mac: str, action: str) -> None:
        if action == "rename":
            self._rename(mac)
        elif action == "temporary":
            self._ask_temporary(mac)
        else:
            self.requestServiceAction.emit({"action": action, "mac": mac})

    def _rename(self, mac: str) -> None:
        current = self._settings.device_name(mac) or ""
        text, ok = QInputDialog.getText(self, "Renommer l'appareil", "Nom (laisser vide pour effacer) :", text=current)
        if ok:
            self._settings.rename_device(mac, text)
            self._settings.save()
            self._render_devices()

    def _ask_temporary(self, mac: str) -> None:
        who = self._settings.device_name(mac) or mac
        dialog = DurationDialog(
            self, "Accès temporaire", f"{who} sera autorisé pendant la durée choisie, puis bloqué automatiquement."
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.requestServiceAction.emit({"action": "allow_for", "mac": mac, "seconds": dialog.seconds()})

    def _on_strict(self, enabled: bool) -> None:
        self.requestServiceAction.emit({"action": "set_default_mode", "mode": "strict" if enabled else "open"})

    def _on_reset_usage(self) -> None:
        answer = QMessageBox.question(self, "WifiManager", "Remettre à zéro les compteurs de données ?")
        if answer == QMessageBox.StandardButton.Yes:
            self.requestServiceAction.emit({"action": "reset_usage"})

    def _on_blocking_clicked(self) -> None:
        if self._service is None:
            self._on_install()
        else:
            self._on_uninstall()

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
    icon = app_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    theme.apply(app)
    theme.watch(app)
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
