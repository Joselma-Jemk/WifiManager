"""Thème visuel : couleurs claires et sombres, feuille de style Qt. Suit le thème de Windows."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    card: str
    border: str
    text: str
    muted: str
    hover: str
    accent: str
    accent_hover: str
    accent_text: str
    toggle_off: str
    success: str
    success_bg: str
    warning: str
    warning_bg: str
    danger: str
    danger_bg: str
    info: str
    info_bg: str


LIGHT = Theme(
    name="clair", bg="#f3f4f6", card="#ffffff", border="#e5e7eb", text="#111827", muted="#6b7280", hover="#f3f4f6",
    accent="#2563eb", accent_hover="#1d4ed8", accent_text="#ffffff", toggle_off="#c3c8d0",
    success="#15803d", success_bg="#dcfce7", warning="#b45309", warning_bg="#fef3c7",
    danger="#b91c1c", danger_bg="#fee2e2", info="#1d4ed8", info_bg="#dbeafe",
)

DARK = Theme(
    name="sombre", bg="#17181b", card="#232428", border="#34363c", text="#f3f4f6", muted="#9ca3af", hover="#2d2f35",
    accent="#3b82f6", accent_hover="#60a5fa", accent_text="#ffffff", toggle_off="#4b4e57",
    success="#4ade80", success_bg="#14351f", warning="#fbbf24", warning_bg="#3b2a0b",
    danger="#f87171", danger_bg="#3d1717", info="#93c5fd", info_bg="#172a4a",
)

_current = LIGHT


def current() -> Theme:
    """Thème actif : lu par les composants qui se dessinent eux-mêmes (interrupteur, avatar)."""
    return _current


def detect() -> Theme:
    """Thème de Windows : sombre si le système l'est, clair sinon (ou si Qt ne sait pas le dire)."""
    scheme = QGuiApplication.styleHints().colorScheme()
    return DARK if scheme == Qt.ColorScheme.Dark else LIGHT


def stylesheet(t: Theme) -> str:
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 10pt; color: {t.text}; }}
QMainWindow, QDialog, QWidget#page, QScrollArea {{ background: {t.bg}; }}
QScrollArea {{ border: none; }}
QFrame#card {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 14px; }}
QLabel {{ background: transparent; }}
QLabel#title {{ font-size: 21pt; font-weight: 700; }}
QLabel#ssid {{ font-size: 19pt; font-weight: 700; }}
QLabel#h2 {{ font-size: 12pt; font-weight: 600; }}
QLabel[role="value"] {{ font-size: 19pt; font-weight: 700; }}
QLabel[role="name"] {{ font-size: 11pt; font-weight: 600; }}
QLabel[muted="true"] {{ color: {t.muted}; }}
QPushButton {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 9px; padding: 8px 16px; }}
QPushButton:hover {{ background: {t.hover}; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.bg}; }}
QPushButton[primary="true"] {{ background: {t.accent}; color: {t.accent_text}; border: none; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {t.accent_hover}; }}
QPushButton[primary="true"]:disabled {{ background: {t.toggle_off}; color: {t.accent_text}; }}
QPushButton[small="true"] {{ padding: 5px 12px; border-radius: 8px; }}
QToolButton {{ background: transparent; border: none; border-radius: 9px; padding: 2px 10px; font-size: 17pt; font-weight: 700; }}
QToolButton:hover {{ background: {t.hover}; }}
QToolButton::menu-indicator {{ image: none; }}
QMenu {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 8px 26px 8px 14px; border-radius: 7px; }}
QMenu::item:selected {{ background: {t.hover}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QLineEdit, QSpinBox {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 9px; padding: 7px 11px; }}
QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {t.accent}; }}
QRadioButton, QCheckBox {{ background: transparent; spacing: 8px; }}
QStatusBar {{ background: {t.bg}; color: {t.muted}; }}
QStatusBar::item {{ border: none; }}
QMessageBox {{ background: {t.bg}; }}
QToolTip {{ background: {t.card}; color: {t.text}; border: 1px solid {t.border}; padding: 5px 8px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def apply(app: QApplication) -> Theme:
    """Applique le thème de Windows à toute l'application."""
    global _current
    _current = detect()
    app.setStyle("Fusion")  # base neutre, que la feuille de style habille de façon identique partout
    app.setStyleSheet(stylesheet(_current))
    return _current


def watch(app: QApplication) -> None:
    """Suit les changements de thème de Windows pendant que l'application tourne."""
    app.styleHints().colorSchemeChanged.connect(lambda _scheme: apply(app))
