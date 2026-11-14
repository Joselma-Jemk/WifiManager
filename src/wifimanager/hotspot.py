"""Pilotage du hotspot mobile de Windows (API officielle NetworkOperatorTetheringManager).

Lecture : état, clients connectés. Écriture : démarrer, arrêter, changer nom et mot de passe.
Le mot de passe actuel n'est jamais lu : on ne sait que le remplacer.
"""

from __future__ import annotations

from typing import Any

from wifimanager.models import Client, HotspotState, normalize_mac
from wifimanager.neighbors import ArpReader, parse_arp, run_arp
from wifimanager.passwords import validate_passphrase, validate_ssid
from wifimanager.ps import PowerShellError, Runner, run_json, run_powershell

# Types WinRT et accès au gestionnaire du hotspot. Suffisant pour la lecture.
_BASE = r"""
$null = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
$null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
function Get-Manager {
    $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    if ($null -eq $profile) { return $null }
    return [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
}
"""

# Attente des opérations asynchrones (méthode classique de PowerShell 5.1). Lent à charger : réservé aux écritures.
_ASYNC = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$asTaskAction = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
function Wait-Operation($operation, $resultType) {
    $task = $asTaskGeneric.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $null = $task.Wait(-1)
    return $task.Result
}
function Wait-Action($action) {
    $task = $asTaskAction.Invoke($null, @($action))
    $null = $task.Wait(-1)
}
"""

_READ_SCRIPT = _BASE + r"""
$manager = Get-Manager
if ($null -eq $manager) {
    @{ available = $false; reason = 'Aucune connexion Internet à partager' } | ConvertTo-Json -Compress
    return
}
$config = $manager.GetCurrentAccessPointConfiguration()
$clients = @()
foreach ($client in $manager.GetTetheringClients()) {
    $names = @()
    foreach ($hostName in $client.HostNames) { $names += $hostName.DisplayName }
    $clients += @{ mac = $client.MacAddress; names = $names }
}
@{
    available = $true
    state = [string]$manager.TetheringOperationalState
    ssid = $config.Ssid
    band = [string]$config.Band
    client_count = [int]$manager.ClientCount
    max_clients = [int]$manager.MaxClientCount
    clients = $clients
} | ConvertTo-Json -Compress -Depth 5
"""

_START_SCRIPT = _BASE + _ASYNC + r"""
$manager = Get-Manager
if ($null -eq $manager) { @{ ok = $false; error = 'Aucune connexion Internet à partager' } | ConvertTo-Json -Compress; return }
$result = Wait-Operation ($manager.StartTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
@{ ok = ([string]$result.Status -eq 'Success'); status = [string]$result.Status; error = $result.AdditionalErrorMessage } | ConvertTo-Json -Compress
"""

_STOP_SCRIPT = _BASE + _ASYNC + r"""
$manager = Get-Manager
if ($null -eq $manager) { @{ ok = $false; error = 'Aucune connexion Internet à partager' } | ConvertTo-Json -Compress; return }
$result = Wait-Operation ($manager.StopTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
@{ ok = ([string]$result.Status -eq 'Success'); status = [string]$result.Status; error = $result.AdditionalErrorMessage } | ConvertTo-Json -Compress
"""

# Le nom et le mot de passe arrivent par l'environnement, jamais dans le script.
_CONFIGURE_SCRIPT = _BASE + _ASYNC + r"""
$manager = Get-Manager
if ($null -eq $manager) { @{ ok = $false; error = 'Aucune connexion Internet à partager' } | ConvertTo-Json -Compress; return }
$config = $manager.GetCurrentAccessPointConfiguration()
$config.Ssid = $env:WIFIMANAGER_SSID
$config.Passphrase = $env:WIFIMANAGER_PASSPHRASE
Wait-Action ($manager.ConfigureAccessPointAsync($config))
@{ ok = $true } | ConvertTo-Json -Compress
"""


def read_state(runner: Runner = run_powershell, arp_reader: ArpReader = run_arp) -> HotspotState:
    """État actuel du hotspot et liste des appareils connectés, avec leur adresse IP quand elle est connue."""
    data = run_json(_READ_SCRIPT, runner)
    if not data.get("available", False):
        return HotspotState(available=False, reason=str(data.get("reason", "Indisponible")))
    clients = _parse_clients(data.get("clients"))
    if clients:
        ips = parse_arp(arp_reader())
        clients = [Client(mac=c.mac, names=c.names, ip=ips.get(c.mac)) for c in clients]
    return HotspotState(
        available=True,
        state=str(data.get("state", "Unknown")),
        ssid=str(data.get("ssid", "")),
        band=str(data.get("band", "")),
        client_count=int(data.get("client_count", 0)),
        max_clients=int(data.get("max_clients", 0)),
        clients=tuple(clients),
    )


def _parse_clients(raw: Any) -> list[Client]:
    if isinstance(raw, dict):  # PowerShell 5.1 écrit parfois un tableau d'un élément comme un simple objet
        raw = [raw]
    clients: list[Client] = []
    for item in raw or []:
        try:
            mac = normalize_mac(str(item.get("mac", "")))
        except ValueError:
            continue  # une adresse illisible ne doit pas faire échouer toute la lecture
        names = item.get("names") or []
        if isinstance(names, str):
            names = [names]
        clients.append(Client(mac=mac, names=tuple(str(name) for name in names if name)))
    return clients


def start(runner: Runner = run_powershell) -> None:
    _check(run_json(_START_SCRIPT, runner), "Démarrage du hotspot impossible")


def stop(runner: Runner = run_powershell) -> None:
    _check(run_json(_STOP_SCRIPT, runner), "Arrêt du hotspot impossible")


def set_credentials(ssid: str, passphrase: str, runner: Runner = run_powershell) -> None:
    """Change le nom et le mot de passe du hotspot. Tous les appareils connectés devront se reconnecter."""
    env = {"WIFIMANAGER_SSID": validate_ssid(ssid), "WIFIMANAGER_PASSPHRASE": validate_passphrase(passphrase)}
    _check(run_json(_CONFIGURE_SCRIPT, runner, env=env), "Changement du mot de passe impossible")


def _check(result: dict[str, Any], message: str) -> None:
    if not result.get("ok", False):
        detail = result.get("error") or result.get("status") or "raison inconnue"
        raise PowerShellError(f"{message} : {detail}")
