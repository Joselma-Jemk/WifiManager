"""Client du service : envoie une demande sur le canal local et attend la réponse. Utilisé par la fenêtre."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtNetwork import QLocalSocket

from wifimanager.service import MAX_MESSAGE_BYTES, pipe_name

CONNECT_MS = 1500
REPLY_MS = 8000


class ServiceUnavailable(Exception):
    """Le service n'écoute pas : il n'est pas installé, pas lancé, ou il s'est arrêté."""


def request(payload: dict[str, Any], timeout_ms: int = REPLY_MS) -> dict[str, Any]:
    """Envoie une demande (bloquant, à appeler depuis le fil du travailleur) et renvoie la réponse du service."""
    socket = QLocalSocket()
    socket.connectToServer(pipe_name())
    if not socket.waitForConnected(CONNECT_MS):
        raise ServiceUnavailable("Le service de blocage n'est pas lancé")
    socket.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    socket.flush()

    data = bytearray()
    while b"\n" not in data and len(data) <= MAX_MESSAGE_BYTES * 8:
        if not socket.waitForReadyRead(timeout_ms):
            break
        data += bytes(socket.readAll())
    socket.disconnectFromServer()
    if b"\n" not in data:
        raise ServiceUnavailable("Le service n'a pas répondu à temps")
    try:
        reply = json.loads(bytes(data).split(b"\n", 1)[0].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ServiceUnavailable("Réponse illisible du service") from exc
    if not isinstance(reply, dict):
        raise ServiceUnavailable("Réponse inattendue du service")
    return reply
