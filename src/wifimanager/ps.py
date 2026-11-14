"""Exécution de scripts PowerShell : la seule porte vers l'API Windows du hotspot (WinRT).

Les scripts sont passés en `-EncodedCommand` (UTF-16 en base64) : aucun problème de guillemets, et la sortie
est forcée en UTF-8 pour ne pas dépendre de la page de code de la console.

Les valeurs secrètes (mot de passe Wi-Fi) ne passent JAMAIS dans le script ni dans la ligne de commande, que les
autres programmes de la session peuvent lire : elles voyagent par variables d'environnement.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from collections.abc import Callable
from typing import Any

TIMEOUT_SECONDS = 30
# Sans ce drapeau, un programme console lancé depuis une application sans console (pythonw, le service, l'exécutable
# installé) fait apparaître une fenêtre qui s'ouvre et se referme aussitôt. TOUT lancement de programme doit le porter.
CREATE_NO_WINDOW = 0x08000000
_PREAMBLE = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n$ErrorActionPreference = 'Stop'\n"

# Un « exécuteur » prend un script (et des variables d'environnement) et renvoie son affichage.
# Il est injectable pour tester sans Windows.
Runner = Callable[..., str]


class PowerShellError(Exception):
    """Le script PowerShell a échoué ou n'a pas répondu à temps."""


def run_powershell(script: str, timeout: float = TIMEOUT_SECONDS, env: dict[str, str] | None = None) -> str:
    encoded = base64.b64encode((_PREAMBLE + script).encode("utf-16-le")).decode("ascii")
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            env={**os.environ, **env} if env else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise PowerShellError(f"PowerShell n'a pas répondu en {timeout:.0f} s") from exc
    except OSError as exc:
        raise PowerShellError(f"PowerShell est introuvable : {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        raise PowerShellError(detail[0] if detail else f"code de sortie {completed.returncode}")
    return completed.stdout


def run_json(script: str, runner: Runner = run_powershell, env: dict[str, str] | None = None) -> Any:
    """Exécute un script qui affiche un seul objet JSON, et le décode."""
    output = (runner(script, env=env) if env else runner(script)).strip()
    if not output:
        raise PowerShellError("Réponse vide de PowerShell")
    try:
        return json.loads(output.splitlines()[-1])
    except json.JSONDecodeError as exc:
        raise PowerShellError(f"Réponse illisible de PowerShell : {output[:200]}") from exc
