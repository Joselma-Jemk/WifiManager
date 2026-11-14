"""PowerShell persistant : un seul processus reste ouvert, pour ne pas payer son démarrage à chaque lecture.

Les commandes arrivent sur l'entrée standard, encodées en base64 : ni guillemets à protéger, ni secret dans une ligne de
commande (que les autres programmes de la session peuvent lire). Chaque réponse se termine par un marqueur.
Si PowerShell ne répond plus, le processus est tué et sera relancé à l'appel suivant.
"""

from __future__ import annotations

import base64
import json
import queue
import subprocess
import threading

from wifimanager.ps import TIMEOUT_SECONDS, PowerShellError

_MARKER = "@@WIFIMANAGER_END@@"
_CREATE_NO_WINDOW = 0x08000000

_SERVER_LOOP = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$marker = '@@WIFIMANAGER_END@@'
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    $request = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($line)) | ConvertFrom-Json
    foreach ($property in $request.env.PSObject.Properties) {
        [Environment]::SetEnvironmentVariable($property.Name, [string]$property.Value, 'Process')
    }
    try {
        $output = & ([scriptblock]::Create($request.script)) 2>&1 | Out-String
        [Console]::Out.WriteLine($output.TrimEnd())
    } catch {
        [Console]::Out.WriteLine((@{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress))
    } finally {
        foreach ($property in $request.env.PSObject.Properties) {
            [Environment]::SetEnvironmentVariable($property.Name, $null, 'Process')
        }
    }
    [Console]::Out.WriteLine($marker)
    [Console]::Out.Flush()
}
"""


class PowerShellSession:
    """Exécuteur compatible avec `wifimanager.ps.Runner` : `session(script, env=None) -> sortie`."""

    def __init__(self, timeout: float = TIMEOUT_SECONDS) -> None:
        self._timeout = timeout
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()

    # --- cycle de vie --------------------------------------------------------------

    def _start(self) -> None:
        encoded = base64.b64encode(_SERVER_LOOP.encode("utf-16-le")).decode("ascii")
        self._lines = queue.Queue()
        self._process = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-OutputFormat",
                "Text",  # sans cela, la sortie redirigée commence par « #< CLIXML »
                "-EncodedCommand",
                encoded,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._read_output, args=(self._process, self._lines), daemon=True).start()

    @staticmethod
    def _read_output(process: subprocess.Popen[str], lines: queue.Queue[str | None]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line.rstrip("\r\n"))
        lines.put(None)  # fin du flux : le processus s'est arrêté

    def close(self) -> None:
        with self._lock:
            self._kill()

    def _kill(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.kill()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # --- exécution --------------------------------------------------------------------

    def __call__(self, script: str, env: dict[str, str] | None = None) -> str:
        with self._lock:
            if not self.running:
                self._start()
            assert self._process is not None and self._process.stdin is not None
            payload = json.dumps({"script": script, "env": env or {}})
            request = base64.b64encode(payload.encode("utf-8")).decode("ascii")
            try:
                self._process.stdin.write(request + "\n")
                self._process.stdin.flush()
            except OSError as exc:
                self._kill()
                raise PowerShellError(f"PowerShell ne répond plus : {exc}") from exc
            return self._read_response()

    def _read_response(self) -> str:
        collected: list[str] = []
        while True:
            try:
                line = self._lines.get(timeout=self._timeout)
            except queue.Empty:
                self._kill()
                raise PowerShellError(f"PowerShell n'a pas répondu en {self._timeout:.0f} s") from None
            if line is None:
                self._kill()
                raise PowerShellError("PowerShell s'est arrêté pendant la commande")
            if line == _MARKER:
                return "\n".join(collected)
            collected.append(line)
