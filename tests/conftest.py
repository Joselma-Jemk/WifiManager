"""Réglages communs : Qt hors écran, et aucun test n'écrit dans les vraies données de l'utilisateur."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WIFIMANAGER_HOME", str(tmp_path / "data"))


@pytest.fixture(scope="session")
def qapp():
    """Une seule application Qt pour toute la session, démontée proprement.

    Sans ce démontage, Qt abandonne le processus à la sortie (« Fatal Python error: Aborted ») alors que tous les tests
    ont réussi, ce qui fait échouer le CI.
    """
    import gc

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    app.processEvents()
    gc.collect()
    app.processEvents()
