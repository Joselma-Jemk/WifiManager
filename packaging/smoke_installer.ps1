# Test de bout en bout de l'installateur, réservé au CI.
# Installe en silence, vérifie les fichiers, lance l'exécutable, puis désinstalle et vérifie le nettoyage.
# ATTENTION : ce script installe dans Program Files et crée une tâche planifiée.
# Il refuse donc de tourner ailleurs que sur GitHub Actions, pour ne jamais toucher à une vraie installation.
param([Parameter(Mandatory = $true)][string]$Setup)

$ErrorActionPreference = "Stop"
if ($env:GITHUB_ACTIONS -ne "true") {
    throw "Ce test installe dans Program Files et crée une tâche planifiée : il ne tourne que sur GitHub Actions."
}

$installDir = Join-Path $env:ProgramFiles "WifiManager"
$log = Join-Path $env:TEMP "wifimanager-setup.log"
$taskName = "WifiManagerService"

function Assert-True($condition, $message) {
    if (-not $condition) { throw $message }
}

# 1. Installation silencieuse.
$p = Start-Process -FilePath $Setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$log" -Wait -PassThru
if ($p.ExitCode -ne 0) {
    Get-Content $log -Tail 40
    throw "L'installateur a quitté avec le code $($p.ExitCode)"
}
foreach ($f in "wifimanager.exe", "LICENSE") {
    Assert-True (Test-Path (Join-Path $installDir $f)) "Fichier manquant après installation : $f"
}
Write-Output "OK  installation : fichiers présents dans $installDir"

# 2. Le dossier d'installation n'est pas modifiable par un simple utilisateur (le service tourne en administrateur).
$acl = Get-Acl $installDir
$writable = $acl.Access | Where-Object {
    $_.IdentityReference -match "Users|Everyone|Authenticated" -and
    $_.AccessControlType -eq "Allow" -and
    ($_.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::Write)
}
Assert-True (-not $writable) "Le dossier d'installation est modifiable par des utilisateurs standard"
Write-Output "OK  droits du dossier d'installation"

# 3. Le programme démarre et reste en vie.
$app = Start-Process -FilePath (Join-Path $installDir "wifimanager.exe") -PassThru
Start-Sleep -Seconds 8
Assert-True (-not $app.HasExited) "wifimanager.exe a quitté avec le code $($app.ExitCode)"
Stop-Process -Id $app.Id -Force
Write-Output "OK  le programme démarre"

# 4. Simule le service installé par l'application, puis vérifie que la désinstallation le retire.
$action = New-ScheduledTaskAction -Execute (Join-Path $installDir "wifimanager.exe") -Argument "--service"
Register-ScheduledTask -TaskName $taskName -Action $action -Force | Out-Null
Assert-True ($null -ne (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) "Tâche de test non créée"

# 5. Désinstallation silencieuse. Le désinstalleur se relance depuis un dossier temporaire : on attend la fin.
Start-Process -FilePath (Join-Path $installDir "unins000.exe") -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
for ($i = 0; $i -lt 90 -and (Test-Path (Join-Path $installDir "wifimanager.exe")); $i++) { Start-Sleep -Seconds 1 }
Assert-True (-not (Test-Path (Join-Path $installDir "wifimanager.exe"))) "Désinstallation incomplète : wifimanager.exe est toujours là"
Write-Output "OK  désinstallation : fichiers retirés"

# 6. La tâche planifiée du service a été retirée.
Start-Sleep -Seconds 2
Assert-True ($null -eq (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) "La tâche planifiée $taskName est restée"
Write-Output "OK  tâche planifiée retirée"
