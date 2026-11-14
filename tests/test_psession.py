"""PowerShell persistant, sur le vrai PowerShell de Windows : réutilisation, secrets, erreurs, expiration."""

import json
import sys
import time

import pytest

from wifimanager.ps import PowerShellError
from wifimanager.psession import PowerShellSession

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell de Windows requis")


@pytest.fixture
def session():
    s = PowerShellSession(timeout=20)
    yield s
    s.close()


def test_returns_script_output(session) -> None:
    assert json.loads(session("@{ ok = $true; n = 3 } | ConvertTo-Json -Compress")) == {"ok": True, "n": 3}


def test_same_process_is_reused_and_second_call_is_fast(session) -> None:
    session("1")
    pid = session._process.pid
    start = time.perf_counter()
    session("2")
    assert session._process.pid == pid
    assert time.perf_counter() - start < 2.0  # pas de nouveau démarrage de PowerShell


def test_unicode_and_multiline_scripts(session) -> None:
    script = "$texte = 'été à Cotonou'\nif ($true) {\n    $texte\n}"
    assert session(script) == "été à Cotonou"


def test_environment_reaches_the_script_then_is_erased(session) -> None:
    assert session("$env:WIFIMANAGER_TEST_SECRET", env={"WIFIMANAGER_TEST_SECRET": "valeur-secrete"}) == "valeur-secrete"
    assert session("[string]$env:WIFIMANAGER_TEST_SECRET") == ""  # effacé après l'appel


def test_script_error_becomes_error_json_not_a_crash(session) -> None:
    reply = json.loads(session("throw 'panne volontaire'"))
    assert reply["ok"] is False and "panne volontaire" in reply["error"]
    assert session("'toujours vivant'") == "toujours vivant"  # la session reste utilisable


def test_timeout_kills_the_process_and_the_next_call_restarts_it() -> None:
    session = PowerShellSession(timeout=2)
    try:
        with pytest.raises(PowerShellError, match="n'a pas répondu"):
            session("Start-Sleep -Seconds 30")
        assert not session.running
        session._timeout = 20
        assert session("'reparti'") == "reparti"
    finally:
        session.close()


def test_close_stops_the_process(session) -> None:
    session("1")
    assert session.running
    session.close()
    assert not session.running
