"""Installation du service de blocage : une tâche planifiée qui le lance à l'ouverture de session, avec les droits élevés.

Une seule fenêtre UAC à l'installation. Ensuite le service démarre tout seul à chaque ouverture de session, sans
rien demander. La désinstallation retire la tâche et arrête le service.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

from wifimanager.ps import (
    CREATE_NO_WINDOW,
    PowerShellError,
    Runner,
    run_json,
    run_powershell,
)

TASK_NAME = "WifiManagerService"
ELEVATION_TIMEOUT_SECONDS = 120


def service_command() -> tuple[str, str]:
    """Programme et arguments qui lancent le service : l'interpréteur sans console, ou l'exécutable installé."""
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return str(Path(sys.argv[0]).resolve()), "--service"
    executable = Path(sys.executable)
    windowed = executable.with_name("pythonw.exe")
    return str(windowed if windowed.exists() else executable), "-m wifimanager.service"


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def install_script(executable: str, arguments: str) -> str:
    """Script exécuté en administrateur : enregistre la tâche et la démarre."""
    return f"""
$ErrorActionPreference = 'Stop'
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute {_quote(executable)} -Argument {_quote(arguments)}
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName {_quote(TASK_NAME)} -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName {_quote(TASK_NAME)}
"""


def uninstall_script() -> str:
    return f"""
$ErrorActionPreference = 'SilentlyContinue'
Stop-ScheduledTask -TaskName {_quote(TASK_NAME)}
Unregister-ScheduledTask -TaskName {_quote(TASK_NAME)} -Confirm:$false
"""


def is_installed(runner: Runner = run_powershell) -> bool:
    """La tâche existe-t-elle ? (lisible sans droits administrateur)"""
    script = (
        f"$t = Get-ScheduledTask -TaskName {_quote(TASK_NAME)} -ErrorAction SilentlyContinue\n"
        "@{ installed = ($null -ne $t) } | ConvertTo-Json -Compress"
    )
    try:
        return bool(run_json(script, runner).get("installed"))
    except PowerShellError:
        return False


def start_service(runner: Runner = run_powershell) -> None:
    """Relance le service déjà installé. Ne demande pas de droits : la tâche appartient à l'utilisateur."""
    script = (
        "try { Start-ScheduledTask -TaskName " + _quote(TASK_NAME) + " -ErrorAction Stop; "
        "@{ ok = $true } | ConvertTo-Json -Compress } "
        "catch { @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress }"
    )
    reply = run_json(script, runner)
    if not reply.get("ok"):
        raise PowerShellError(str(reply.get("error", "démarrage du service impossible")))


def run_elevated(script: str, timeout: float = ELEVATION_TIMEOUT_SECONDS) -> None:
    """Exécute un script PowerShell en administrateur. Windows affiche sa fenêtre UAC ; refusée, on lève une erreur."""
    inner = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    launcher = (
        "try { $p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
        f"-ArgumentList '-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand','{inner}'; "
        "exit $p.ExitCode } catch { exit 1223 }"  # 1223 : l'utilisateur a refusé l'élévation
    )
    outer = base64.b64encode(launcher.encode("utf-16-le")).decode("ascii")
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", outer],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=CREATE_NO_WINDOW,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        raise PowerShellError("La fenêtre d'autorisation Windows n'a pas été validée à temps") from exc
    if completed.returncode == 1223:
        raise PowerShellError("Autorisation administrateur refusée")
    if completed.returncode != 0:
        raise PowerShellError(f"Le script administrateur a échoué (code {completed.returncode})")


def install_service() -> None:
    executable, arguments = service_command()
    run_elevated(install_script(executable, arguments))


def uninstall_service() -> None:
    run_elevated(uninstall_script())
