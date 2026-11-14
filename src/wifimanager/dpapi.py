"""Protection d'un secret avec DPAPI : seul le compte Windows de l'utilisateur peut le relire, sur cette machine.

Sert à garder le mot de passe du hotspot pour réafficher le QR code. Le fichier de réglages ne contient jamais ce mot de
passe en clair.
"""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import POINTER, Structure, byref, c_ubyte, c_uint32, c_void_p


class DpapiError(Exception):
    """Le secret n'a pas pu être protégé ou relu (autre compte, autre machine, données abîmées)."""


class _Blob(Structure):
    _fields_ = [("cbData", c_uint32), ("pbData", POINTER(c_ubyte))]


_ENTROPY = b"WifiManager/hotspot-password"  # lie le secret à cette application


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    """Structure DPAPI pointant vers une copie des octets. Le tampon doit rester vivant : on le renvoie aussi."""
    buffer = (c_ubyte * len(data)).from_buffer_copy(data)
    return _Blob(len(data), ctypes.cast(buffer, POINTER(c_ubyte))), buffer


def _take(blob: _Blob) -> bytes:
    """Copie les octets rendus par Windows, puis libère sa mémoire."""
    data = ctypes.string_at(blob.pbData, blob.cbData)
    local_free = ctypes.windll.kernel32.LocalFree
    local_free.argtypes = [c_void_p]  # sans cela, le pointeur serait tronqué sur 64 bits
    local_free(ctypes.cast(blob.pbData, c_void_p))
    return data


def protect(secret: str) -> str:
    """Secret chiffré pour le compte courant, en base64."""
    if sys.platform != "win32":
        raise DpapiError("DPAPI n'est disponible que sous Windows")
    source, _keep_source = _blob(secret.encode("utf-8"))
    entropy, _keep_entropy = _blob(_ENTROPY)
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(byref(source), "WifiManager", byref(entropy), None, None, 0, byref(out)):
        raise DpapiError("Protection du secret impossible")
    return base64.b64encode(_take(out)).decode("ascii")


def unprotect(protected: str) -> str:
    """Relit un secret protégé par `protect`. Lève DpapiError s'il vient d'un autre compte ou est abîmé."""
    if sys.platform != "win32":
        raise DpapiError("DPAPI n'est disponible que sous Windows")
    try:
        raw = base64.b64decode(protected.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise DpapiError("Secret protégé illisible") from exc
    source, _keep_source = _blob(raw)
    entropy, _keep_entropy = _blob(_ENTROPY)
    out = _Blob()
    description = ctypes.c_wchar_p()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        byref(source), byref(description), byref(entropy), None, None, 0, byref(out)
    ):
        raise DpapiError("Ce secret ne peut pas être relu avec ce compte")
    return _take(out).decode("utf-8")
