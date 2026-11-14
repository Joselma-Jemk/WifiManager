"""Dialogues : mot de passe, QR code, durée d'un accès, pass invité."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from wifimanager.formatting import duration_label, format_remaining
from wifimanager.passwords import (
    generate_passphrase,
    validate_passphrase,
    validate_ssid,
)
from wifimanager.qr import render_qr, wifi_payload

CLIPBOARD_CLEAR_MS = 30_000
PRESET_HOURS = (6, 12, 24)
MAX_CUSTOM_HOURS = 168  # 7 jours


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
    """QR code et mot de passe du hotspot, pour qu'un téléphone rejoigne le réseau sans rien taper."""

    def __init__(self, parent: QWidget | None, ssid: str, passphrase: str, title: str = "Connecter un appareil") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._passphrase = passphrase
        self._copied: str | None = None

        qr = QLabel()
        qr.setPixmap(render_qr(wifi_payload(ssid, passphrase)))
        qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info = QLabel(f"<b>{_escape(ssid)}</b><br>Mot de passe : <b>{_escape(passphrase)}</b>")
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("Scanne le QR code avec l'appareil photo du téléphone pour rejoindre le réseau.")
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


class DurationDialog(QDialog):
    """Choix d'une durée d'accès : 6 h, 12 h, 24 h, ou un nombre d'heures libre."""

    def __init__(self, parent: QWidget | None, title: str, intro: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._group = QButtonGroup(self)
        layout = QVBoxLayout(self)
        intro_label = QLabel(intro)
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        self._presets: dict[int, QRadioButton] = {}
        for hours in PRESET_HOURS:
            button = QRadioButton(f"{hours} heures")
            self._group.addButton(button)
            self._presets[hours] = button
            layout.addWidget(button)
        self._presets[PRESET_HOURS[0]].setChecked(True)

        self._custom_radio = QRadioButton("Autre durée :")
        self._group.addButton(self._custom_radio)
        self._custom_hours = QSpinBox()
        self._custom_hours.setRange(1, MAX_CUSTOM_HOURS)
        self._custom_hours.setSuffix(" h")
        self._custom_hours.setValue(48)
        self._custom_hours.setEnabled(False)  # actif seulement quand « Autre durée » est choisi : pas d'ambiguïté
        self._custom_radio.toggled.connect(self._custom_hours.setEnabled)
        custom_row = QHBoxLayout()
        custom_row.addWidget(self._custom_radio)
        custom_row.addWidget(self._custom_hours)
        custom_row.addStretch()
        layout.addLayout(custom_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Valider")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def seconds(self) -> int:
        """Durée choisie, en secondes."""
        if self._custom_radio.isChecked():
            return self._custom_hours.value() * 3600
        for hours, button in self._presets.items():
            if button.isChecked():
                return hours * 3600
        return PRESET_HOURS[0] * 3600


class PassDialog(QDialog):
    """Pass invité : QR code du hotspot, avec le compte à rebours de sa validité et l'état du pass."""

    revokeRequested = Signal(str)

    def __init__(self, parent: QWidget | None, ssid: str, passphrase: str, guest: dict[str, Any]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pass invité")
        self.setMinimumWidth(400)
        self._id = str(guest["id"])
        self._deadline = float(guest["claim_deadline"])
        self._finished = False

        qr = QLabel()
        qr.setPixmap(render_qr(wifi_payload(ssid, passphrase), size=240))
        qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel(f"<b>Pass invité de {duration_label(int(guest['duration']))}</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info = QLabel(f"<b>{_escape(ssid)}</b><br>Mot de passe : <b>{_escape(passphrase)}</b>")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._state = QLabel()
        self._state.setWordWrap(True)
        self._state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel(
            f"Le premier NOUVEL appareil qui se connecte avec ce QR code reçoit {duration_label(int(guest['duration']))} "
            "d'accès, puis il est bloqué. Un appareil déjà connu n'est pas concerné."
        )
        hint.setWordWrap(True)

        self._revoke = QPushButton("Annuler ce pass")
        self._revoke.clicked.connect(lambda: self.revokeRequested.emit(self._id))
        close = QPushButton("Fermer")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(self._revoke)
        buttons.addStretch()
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(qr)
        layout.addWidget(info)
        layout.addWidget(self._state)
        layout.addWidget(hint)
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def _tick(self) -> None:
        if self._finished:
            return
        remaining = int(self._deadline - time.time())
        if remaining <= 0:
            self.mark_finished("Ce pass n'a pas été utilisé à temps : il est expiré.")
            return
        self._state.setText(f"En attente d'un appareil… valable encore {format_remaining(remaining)}")

    def mark_finished(self, text: str) -> None:
        """Le pass a été réclamé, annulé ou a expiré : on arrête le compte à rebours."""
        self._finished = True
        self._timer.stop()
        self._revoke.setEnabled(False)
        self._state.setText(text)

    def update_from_service(self, service: dict[str, Any] | None) -> None:
        """À appeler à chaque état reçu du service : détecte quand le pass a été réclamé ou a disparu."""
        if self._finished or service is None:
            return
        if any(guest["id"] == self._id for guest in service.get("passes", [])):
            return
        for event in service.get("events", []):
            if event.get("kind") == "pass_claimed" and event.get("detail") == self._id:
                device = next((d for d in service.get("devices", []) if d["mac"] == event.get("mac")), None)
                name = (device["names"][0] if device and device.get("names") else event.get("mac", "un appareil"))
                self.mark_finished(f"Pass utilisé par {name}. Son accès est maintenant limité dans le temps.")
                return
        self.mark_finished("Ce pass n'est plus disponible (annulé ou expiré).")
