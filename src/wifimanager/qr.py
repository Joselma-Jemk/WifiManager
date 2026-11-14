"""QR code de connexion Wi-Fi : le téléphone qui le scanne rejoint le réseau sans taper le mot de passe."""

from __future__ import annotations

import segno
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

QUIET_ZONE = 4  # modules blancs autour du code, exigés par la norme pour une lecture fiable
_ESCAPED = str.maketrans({"\\": "\\\\", ";": "\\;", ",": "\\,", ":": "\\:", '"': '\\"'})


def wifi_payload(ssid: str, passphrase: str) -> str:
    """Texte du QR code au format `WIFI:T:WPA;S:nom;P:mot-de-passe;;`, avec les caractères spéciaux protégés."""
    return f"WIFI:T:WPA;S:{ssid.translate(_ESCAPED)};P:{passphrase.translate(_ESCAPED)};;"


def qr_matrix(payload: str) -> list[list[bool]]:
    """Grille du QR code : `True` pour un module sombre."""
    code = segno.make(payload, error="m", micro=False)
    return [[bool(cell) for cell in row] for row in code.matrix]


def render_qr(payload: str, size: int = 260) -> QPixmap:
    """Image carrée du QR code, avec sa zone blanche, aux modules nets."""
    matrix = qr_matrix(payload)
    modules = len(matrix) + 2 * QUIET_ZONE
    scale = max(1, size // modules)
    side = modules * scale
    pixmap = QPixmap(side, side)
    pixmap.fill(QColor("white"))
    painter = QPainter(pixmap)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("black"))
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                painter.drawRect(QRect((x + QUIET_ZONE) * scale, (y + QUIET_ZONE) * scale, scale, scale))
    painter.end()
    return pixmap
