"""Travailleur Qt : parle à PowerShell dans son propre fil, pour que la fenêtre ne se fige jamais.

Il lit l'état à intervalle régulier, applique les ordres de l'utilisateur (démarrer, arrêter, changer le mot de passe),
et laisse la surveillance relancer le hotspot s'il s'éteint tout seul. Tout ce qu'il apprend sort par des signaux.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from wifimanager import hotspot
from wifimanager.models import HotspotState
from wifimanager.neighbors import ArpReader
from wifimanager.ps import PowerShellError, Runner
from wifimanager.watchdog import Action, Watchdog


class HotspotWorker(QObject):
    stateChanged = Signal(object)  # HotspotState
    message = Signal(str)  # information pour la barre d'état
    failed = Signal(str)  # erreur à montrer à l'utilisateur
    busyChanged = Signal(bool)
    credentialsApplied = Signal(str, str)  # nom et mot de passe appliqués, pour afficher le QR code

    def __init__(
        self,
        runner: Runner,
        arp_reader: ArpReader,
        watchdog: Watchdog,
        poll_seconds: int = 10,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner
        self._arp_reader = arp_reader
        self._watchdog = watchdog
        self._poll_seconds = poll_seconds
        self._timer: QTimer | None = None
        self._last_state: HotspotState | None = None

    # --- cycle de vie ---------------------------------------------------------------

    @Slot()
    def start_polling(self) -> None:
        """À appeler depuis le fil du travailleur : le minuteur doit y vivre."""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._timer.start(self._poll_seconds * 1000)
        self.poll()

    @Slot()
    def shutdown(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    # --- lecture et surveillance ----------------------------------------------------------

    @Slot()
    def poll(self) -> None:
        state = self._read()
        if state is None:
            return
        action = self._watchdog.observe(state)
        if action is Action.RESTART:
            self.message.emit(
                f"Le hotspot s'est éteint : relance automatique (essai {self._watchdog.attempts}"
                f"/{self._watchdog.config.max_attempts})"
            )
            self._start_hotspot(automatic=True)
        elif action is Action.GIVE_UP:
            self.failed.emit(
                f"Le hotspot ne repart pas après {self._watchdog.config.max_attempts} essais. "
                "Nouvelle tentative dans quelques minutes, ou relance-le toi-même."
            )

    def _read(self) -> HotspotState | None:
        try:
            state = hotspot.read_state(self._runner, self._arp_reader)
        except PowerShellError as exc:
            self.failed.emit(f"Lecture du hotspot impossible : {exc}")
            return None
        self._last_state = state
        self.stateChanged.emit(state)
        return state

    # --- ordres de l'utilisateur ------------------------------------------------------------

    @Slot()
    def start_hotspot(self) -> None:
        self._watchdog.user_started()
        self._start_hotspot(automatic=False)

    def _start_hotspot(self, automatic: bool) -> None:
        self.busyChanged.emit(True)
        try:
            hotspot.start(self._runner)
        except PowerShellError as exc:
            self.failed.emit(str(exc))
        else:
            if not automatic:
                self.message.emit("Hotspot démarré")
        finally:
            self.busyChanged.emit(False)
        self._read()

    @Slot()
    def stop_hotspot(self) -> None:
        self._watchdog.user_stopped()  # arrêt voulu : la surveillance ne doit jamais le défaire
        self.busyChanged.emit(True)
        try:
            hotspot.stop(self._runner)
        except PowerShellError as exc:
            self.failed.emit(str(exc))
        else:
            self.message.emit("Hotspot arrêté")
        finally:
            self.busyChanged.emit(False)
        self._read()

    @Slot(str, str)
    def change_credentials(self, ssid: str, passphrase: str) -> None:
        """Change le nom et le mot de passe, puis relance le hotspot pour qu'ils s'appliquent."""
        was_on = self._last_state is not None and self._last_state.is_on
        self.busyChanged.emit(True)
        try:
            hotspot.set_credentials(ssid, passphrase, self._runner)
            if was_on:
                self._watchdog.user_stopped()  # redémarrage voulu : pas de relance automatique en parallèle
                hotspot.stop(self._runner)
                hotspot.start(self._runner)
                self._watchdog.user_started()
        except (PowerShellError, ValueError) as exc:
            self.failed.emit(f"Mot de passe non changé : {exc}")
            if was_on:
                self._watchdog.user_started()
        else:
            self.message.emit("Mot de passe changé : les appareils doivent se reconnecter")
            self.credentialsApplied.emit(ssid, passphrase)
        finally:
            self.busyChanged.emit(False)
        self._read()

    @Slot(bool)
    def set_auto_restart(self, enabled: bool) -> None:
        self._watchdog.config.enabled = enabled
        self.message.emit("Relance automatique activée" if enabled else "Relance automatique désactivée")
