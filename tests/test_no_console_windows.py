"""Aucune fenêtre console ne doit clignoter : tout lancement de programme porte CREATE_NO_WINDOW.

Un programme console lancé depuis une application sans console (pythonw, le service, l'exécutable installé) ouvre sinon
une fenêtre qui apparaît et disparaît aussitôt. C'est ce qui est arrivé avec `arp.exe`, lancé toutes les 5 secondes.
"""

import ast
from pathlib import Path

import pytest

from wifimanager import neighbors, ps

SRC = Path(__file__).resolve().parent.parent / "src" / "wifimanager"
LAUNCHERS = {"run", "Popen", "check_output", "check_call", "call"}


def find_launches_without_flag(source: str) -> list[int]:
    """Numéros des lignes qui lancent un programme avec `subprocess` sans `creationflags`."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value
        if node.func.attr in LAUNCHERS and isinstance(owner, ast.Name) and owner.id == "subprocess":
            if not any(keyword.arg == "creationflags" for keyword in node.keywords):
                lines.append(node.lineno)
    return lines


def test_every_program_launch_in_the_source_hides_its_console_window() -> None:
    problems = {
        f"{path.name}:{line}"
        for path in sorted(SRC.glob("*.py"))
        for line in find_launches_without_flag(path.read_text(encoding="utf-8"))
    }
    assert not problems, f"Lancement sans CREATE_NO_WINDOW (une fenêtre clignoterait) : {sorted(problems)}"


def test_the_scan_really_detects_a_missing_flag() -> None:
    bad = "import subprocess\nsubprocess.run(['arp', '-a'])\n"
    good = "import subprocess\nsubprocess.run(['arp', '-a'], creationflags=0x08000000)\n"
    assert find_launches_without_flag(bad) == [2]
    assert find_launches_without_flag(good) == []
    assert find_launches_without_flag("import subprocess\nsubprocess.Popen(['x'])\n") == [2]


class Recorder:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def __call__(self, *args, **kwargs):
        self.kwargs = kwargs
        import subprocess

        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


@pytest.mark.parametrize("module", [neighbors, ps])
def test_arp_and_powershell_are_launched_without_a_console_window(module, monkeypatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(module.subprocess, "run", recorder)
    if module is neighbors:
        neighbors.run_arp()
    else:
        ps.run_powershell("1")
    assert recorder.kwargs["creationflags"] & ps.CREATE_NO_WINDOW
    assert recorder.kwargs["stdin"] is not None  # pas d'entrée héritée d'une application sans console


def test_the_flag_value_is_the_windows_constant() -> None:
    assert ps.CREATE_NO_WINDOW == 0x08000000
