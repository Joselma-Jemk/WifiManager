"""Mots de passe Wi-Fi : génération lisible et validation WPA2."""

from __future__ import annotations

import secrets

# Sans caractères ambigus (0/O, 1/l/I) : le mot de passe se dicte et se recopie sans erreur.
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
GROUP_LENGTH = 4
GROUPS = 3  # 12 caractères sur 31 symboles, soit environ 59 bits d'entropie

MIN_PASSPHRASE = 8
MAX_PASSPHRASE = 63
MAX_SSID_BYTES = 32


def generate_passphrase() -> str:
    """Mot de passe aléatoire du type « k7mp-4nwx-q2ht »."""
    groups = ("".join(secrets.choice(ALPHABET) for _ in range(GROUP_LENGTH)) for _ in range(GROUPS))
    return "-".join(groups)


def validate_passphrase(passphrase: str) -> str:
    """Mot de passe WPA2-Personnel : 8 à 63 caractères ASCII imprimables."""
    if not MIN_PASSPHRASE <= len(passphrase) <= MAX_PASSPHRASE:
        raise ValueError(f"Le mot de passe doit faire entre {MIN_PASSPHRASE} et {MAX_PASSPHRASE} caractères")
    if not all(32 <= ord(char) <= 126 for char in passphrase):
        raise ValueError("Le mot de passe ne peut contenir que des caractères ASCII imprimables")
    return passphrase


def validate_ssid(ssid: str) -> str:
    if not ssid or len(ssid.encode("utf-8")) > MAX_SSID_BYTES:
        raise ValueError(f"Le nom du réseau doit faire entre 1 et {MAX_SSID_BYTES} octets")
    return ssid
