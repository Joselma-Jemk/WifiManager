"""Composants visuels : interrupteur, pastille, avatar, carte, tuile de synthèse, ligne d'appareil."""

from __future__ import annotations

import re
import zlib
from typing import Any

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from wifimanager import theme
from wifimanager.formatting import access_text, format_remaining, usage_text

NEEDS_SERVICE = "Active d'abord le blocage (service administrateur)"
AVATAR_COLORS = ("#2563eb", "#7c3aed", "#db2777", "#ea580c", "#0d9488", "#4d7c0f", "#b45309", "#0369a1")


class Pill(QLabel):
    """Pastille colorée dessinée à la main : sa taille ne dépend pas du moment où Qt applique la feuille de style."""

    PAD_X = 12
    PAD_Y = 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = "neutral"
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    @property
    def kind(self) -> str:
        return self._kind

    def set_content(self, text: str, kind: str) -> None:
        self.setText(text)
        self._kind = kind
        self.updateGeometry()
        self.update()

    def _pill_font(self) -> QFont:
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(max(8.5, font.pointSizeF() - 1))
        return font

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self._pill_font())
        return QSize(metrics.horizontalAdvance(self.text()) + 2 * self.PAD_X, metrics.height() + 2 * self.PAD_Y)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event: Any) -> None:
        palette = theme.current()
        background, foreground = {
            "success": (palette.success_bg, palette.success),
            "warning": (palette.warning_bg, palette.warning),
            "danger": (palette.danger_bg, palette.danger),
            "info": (palette.info_bg, palette.info),
        }.get(self._kind, (palette.hover, palette.muted))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        radius = self.height() / 2
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)
        painter.setPen(QColor(foreground))
        painter.setFont(self._pill_font())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


def set_pill(label: Pill, text: str, kind: str) -> None:
    """Met un texte en forme de pastille colorée (success, warning, danger, info, neutral)."""
    label.set_content(text, kind)


def muted(label: QLabel) -> QLabel:
    label.setProperty("muted", True)
    return label


def role(label: QLabel, name: str) -> QLabel:
    label.setProperty("role", name)
    return label


class ToggleSwitch(QAbstractButton):
    """Interrupteur à glissière. `clicked` n'est émis que par un clic de l'utilisateur, jamais par `setChecked`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._position = 0.0
        self._animation = QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(140)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(46, 26)

    def _get_position(self) -> float:
        return self._position

    def _set_position(self, value: float) -> None:
        self._position = value
        self.update()

    position = Property(float, _get_position, _set_position)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def set_checked_silently(self, checked: bool) -> None:
        """Change l'état sans animation ni signal : pour refléter l'état réel du hotspot."""
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)
        self._animation.stop()
        self._set_position(1.0 if checked else 0.0)

    def paintEvent(self, _event: Any) -> None:
        palette = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QColor(palette.accent if self.isChecked() else palette.toggle_off)
        if not self.isEnabled():
            track.setAlpha(120)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        height = self.height()
        painter.drawRoundedRect(QRectF(0, 0, self.width(), height), height / 2, height / 2)
        knob = height - 6
        x = 3 + self._position * (self.width() - knob - 6)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(x, 3, knob, knob))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(palette.accent))
            painter.drawRoundedRect(QRectF(0, 0, self.width() - 1, height - 1), height / 2, height / 2)


class Avatar(QWidget):
    """Pastille ronde avec les initiales de l'appareil, d'une couleur propre à son adresse MAC."""

    def __init__(self, size: int = 42, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._text = "?"
        self._color = QColor(AVATAR_COLORS[0])
        self._dimmed = False

    def set_identity(self, name: str | None, mac: str, dimmed: bool = False) -> None:
        """Initiales : premier et dernier mot du nom (« iPhone de Marie » → IM). Sans nom : un point d'interrogation."""
        words = [w for w in re.split(r"[\s\-_.:]+", name or "") if w]
        if not words:
            self._text = "?"
        elif len(words) == 1:
            self._text = words[0][:2].upper()
        else:
            self._text = (words[0][0] + words[-1][0]).upper()
        self._color = QColor(AVATAR_COLORS[zlib.crc32(mac.encode()) % len(AVATAR_COLORS)])
        self._dimmed = dimmed
        self.update()

    @property
    def initials(self) -> str:
        return self._text

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._color)
        if self._dimmed:
            color.setAlpha(110)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(self.rect())
        painter.setPen(QColor("#ffffff"))
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(self.width() * 0.26)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")


class StatTile(Card):
    """Petite carte de synthèse : un titre, une grande valeur, une ligne d'explication, et un bouton facultatif."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = muted(QLabel(title))
        self.value = role(QLabel("—"), "value")
        self.caption = muted(QLabel(""))
        self.caption.setWordWrap(True)
        self.button = QPushButton()
        self.button.setProperty("small", True)
        self.button.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(4)
        layout.addWidget(self._title)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)
        layout.addSpacing(4)
        layout.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()


class DeviceRow(Card):
    """Une ligne par appareil : avatar, nom, adresses, pastille d'accès, données, et menu d'actions."""

    actionRequested = Signal(str, str)  # (adresse MAC, action : block, allow, temporary, forget, rename)

    MENU = (
        ("rename", "Renommer…"),
        ("block", "Bloquer"),
        ("allow", "Autoriser"),
        ("temporary", "Accès temporaire…"),
        ("forget", "Retirer la règle"),
    )

    def __init__(self, mac: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mac = mac
        self._avatar = Avatar()
        self._name = role(QLabel(""), "name")
        self._subtitle = muted(QLabel(""))
        self._pill = Pill()
        self._data = muted(QLabel(""))
        self._data.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._approve = QPushButton("Autoriser")
        self._approve.setProperty("primary", True)
        self._approve.setProperty("small", True)
        self._approve.clicked.connect(lambda: self.actionRequested.emit(self.mac, "allow"))
        self._more = QToolButton()
        self._more.setText("⋯")
        self._more.setToolTip("Actions")
        self._more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self)
        self._actions: dict[str, Any] = {}
        for key, label in self.MENU:
            action = self._menu.addAction(label)
            action.triggered.connect(lambda _checked=False, k=key: self.actionRequested.emit(self.mac, k))
            self._actions[key] = action
        self._more.setMenu(self._menu)

        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(self._name)
        text.addWidget(self._subtitle)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 12)
        layout.setSpacing(14)
        layout.addWidget(self._avatar)
        layout.addLayout(text, stretch=1)
        layout.addWidget(self._pill)
        layout.addWidget(self._approve)
        layout.addWidget(self._data)
        layout.addWidget(self._more)

    # --- lecture (utilisée par les tests) ----------------------------------------------------

    def name(self) -> str:
        return self._name.text()

    def subtitle(self) -> str:
        return self._subtitle.text()

    def access(self) -> str:
        return self._pill.text()

    def data(self) -> str:
        return self._data.text()

    def initials(self) -> str:
        return self._avatar.initials

    def action_enabled(self, key: str) -> bool:
        return self._actions[key].isEnabled()

    def approve_visible(self) -> bool:
        return not self._approve.isHidden()

    # --- affichage ---------------------------------------------------------------------------------

    def update_from(self, device: dict[str, Any], custom_name: str | None, has_service: bool) -> None:
        names = device.get("names") or []
        known = custom_name or (names[0] if names else None)
        label = known or f"Appareil {self.mac[-5:]}"  # sans nom connu : les derniers chiffres de l'adresse MAC suffisent
        connected = bool(device.get("connected"))
        mode = device.get("mode")
        self._name.setText(label)
        self._avatar.set_identity(known, self.mac, dimmed=not connected)
        where = device.get("ip") or "adresse inconnue"
        self._subtitle.setText(f"{where}  ·  {self.mac}" + ("" if connected else "  ·  absent"))

        if not has_service or not mode:
            self._pill.hide()
            self._data.setText("")
        else:
            kind = {"allowed": "success", "blocked": "danger", "pending": "warning", "temporary": "info"}.get(mode, "neutral")
            text = access_text(device)
            if mode == "temporary":
                text = f"Temporaire · {format_remaining(int(device.get('remaining') or 0))}"
            if mode == "pending":
                text = "En attente"
            set_pill(self._pill, text, kind)
            self._pill.show()
            self._data.setText(usage_text(device))
        self._approve.setVisible(has_service and mode == "pending")

        explicit = device.get("explicit") is True
        enabled = {
            "rename": True,
            "block": has_service and mode != "blocked",
            "allow": has_service and mode in ("blocked", "pending", "temporary"),
            "temporary": has_service,
            "forget": has_service and explicit,
        }
        for key, action in self._actions.items():
            action.setEnabled(enabled[key])
        self._more.setToolTip("Actions" if has_service else f"Actions — {NEEDS_SERVICE}")
