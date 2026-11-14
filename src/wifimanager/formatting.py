"""Mise en forme des textes affichés : durées, accès, données. Sans Qt, donc testable."""

from __future__ import annotations

from typing import Any

from wifimanager.usage import format_bytes


def format_remaining(seconds: int) -> str:
    """Durée restante lisible : « 5 h 42 min », « 12 min », « moins d'une minute »."""
    if seconds < 60:
        return "moins d'une minute"
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days} j {hours} h" if hours else f"{days} j"
    if hours:
        return f"{hours} h {minutes:02d} min"
    return f"{minutes} min"


def access_text(device: dict[str, Any]) -> str:
    """Ce que l'utilisateur lit dans la colonne « Accès »."""
    mode = device.get("mode")
    if mode == "temporary":
        return f"Temporaire : {format_remaining(int(device.get('remaining') or 0))} restant"
    if mode == "pending":
        return "En attente de ton accord"
    if mode == "blocked":
        reason = device.get("reason") or ""
        return "Accès expiré" if reason == "accès expiré" else "Bloqué"
    return "Autorisé"


def usage_text(device: dict[str, Any]) -> str:
    """Données d'un appareil : « 1,2 Go reçus · 80 Mo envoyés », suivi de « (estimé) » si elles sont partagées.

    Des mots plutôt que des flèches : Windows affiche les flèches ↓ ↑ comme des emojis en couleurs vives.
    """
    received = format_bytes(int(device.get("downloaded") or 0))
    sent = format_bytes(int(device.get("uploaded") or 0))
    text = f"{received} reçus  ·  {sent} envoyés"
    return text + " (estimé)" if device.get("estimated") else text


def duration_label(seconds: int) -> str:
    """Durée d'un pass : « 6 h », « 12 h », « 24 h », « 3 j », « 90 min »."""
    if seconds % 86400 == 0:
        return f"{seconds // 86400} j"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    return f"{max(1, seconds // 60)} min"
