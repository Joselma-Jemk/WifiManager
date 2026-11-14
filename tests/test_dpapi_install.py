"""Protection du mot de passe (DPAPI réel) et installation du service (scripts et détection)."""

import json
import sys

import pytest

from wifimanager import dpapi, install
from wifimanager.settings import Settings, settings_file

SECRET = "k7mp-4nwx-q2ht"
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI de Windows requis")


# --- DPAPI -----------------------------------------------------------------------------


@windows_only
def test_protect_roundtrip_and_ciphertext_hides_the_secret() -> None:
    protected = dpapi.protect(SECRET)
    assert SECRET not in protected and dpapi.unprotect(protected) == SECRET


@windows_only
def test_unicode_and_empty_like_secrets() -> None:
    assert dpapi.unprotect(dpapi.protect("mot de passe é à ü 日本")) == "mot de passe é à ü 日本"


@windows_only
def test_same_secret_protected_twice_gives_different_ciphertexts() -> None:
    assert dpapi.protect(SECRET) != dpapi.protect(SECRET)  # le sel de DPAPI rend chaque chiffrement unique


@windows_only
@pytest.mark.parametrize("garbage", ["", "pas du base64 !!", "AAAA", "QUJDREVGR0g="])
def test_garbage_is_refused_cleanly(garbage: str) -> None:
    with pytest.raises(dpapi.DpapiError):
        dpapi.unprotect(garbage)


@windows_only
def test_settings_never_store_the_password_in_clear() -> None:
    settings = Settings()
    settings.set_wifi_password(SECRET)
    settings.save()
    assert SECRET not in settings_file().read_text(encoding="utf-8")
    assert Settings.load().wifi_password() == SECRET


@windows_only
def test_settings_with_unreadable_password_give_none_not_an_error() -> None:
    settings = Settings(wifi_password_protected="QUJDREVGR0g=")
    assert settings.wifi_password() is None
    settings.set_wifi_password(None)
    assert settings.wifi_password_protected == "" and settings.wifi_password() is None


def test_settings_without_password() -> None:
    assert Settings().wifi_password() is None


# --- installation du service -------------------------------------------------------------------


def test_install_script_registers_an_elevated_logon_task_and_starts_it() -> None:
    script = install.install_script(r"C:\Python\pythonw.exe", "-m wifimanager.service")
    assert "-RunLevel Highest" in script and "-AtLogOn" in script
    assert "Register-ScheduledTask" in script and "Start-ScheduledTask" in script
    assert "ExecutionTimeLimit ([TimeSpan]::Zero)" in script  # un service ne doit jamais être arrêté après 72 h
    assert "MultipleInstances IgnoreNew" in script


def test_paths_with_quotes_cannot_break_out_of_the_script() -> None:
    script = install.install_script("C:\\Users\\O'Brien\\python.exe", "-m x'; Remove-Item C:\\ -Recurse; '")
    assert "O''Brien" in script
    assert "x''; Remove-Item C:\\ -Recurse; ''" in script  # l'apostrophe est doublée : tout reste du texte


def test_uninstall_script_stops_then_removes_the_task() -> None:
    script = install.uninstall_script()
    assert script.index("Stop-ScheduledTask") < script.index("Unregister-ScheduledTask")


def test_service_command_uses_the_windowless_interpreter_when_available(tmp_path, monkeypatch) -> None:
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.delattr(sys, "frozen", raising=False)
    executable, arguments = install.service_command()
    assert executable.endswith("pythonw.exe") and arguments == "-m wifimanager.service"


def test_is_installed_reads_the_task_state() -> None:
    assert install.is_installed(lambda script, env=None: json.dumps({"installed": True})) is True
    assert install.is_installed(lambda script, env=None: json.dumps({"installed": False})) is False


def test_is_installed_is_false_when_powershell_fails() -> None:
    from wifimanager.ps import PowerShellError

    def broken(script, env=None):
        raise PowerShellError("introuvable")

    assert install.is_installed(broken) is False
