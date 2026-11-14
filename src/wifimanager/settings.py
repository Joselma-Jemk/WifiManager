"""Réglages et noms d'appareils, dans un petit fichier JSON écrit de façon atomique."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from wifimanager.models import normalize_mac

MIN_POLL_SECONDS = 3
MAX_POLL_SECONDS = 300
MAX_NAME_LENGTH = 64


def data_dir() -> Path:
    """Dossier des données. `WIFIMANAGER_HOME` permet de le déplacer (tests, sauvegarde)."""
    override = os.environ.get("WIFIMANAGER_HOME")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "WifiManager"


def settings_file() -> Path:
    return data_dir() / "settings.json"


@dataclass
class Settings:
    auto_restart: bool = False
    poll_seconds: int = 10
    device_names: dict[str, str] = field(default_factory=dict)  # adresse MAC → nom choisi par l'utilisateur

    def device_name(self, mac: str) -> str | None:
        return self.device_names.get(normalize_mac(mac))

    def rename_device(self, mac: str, name: str) -> None:
        key = normalize_mac(mac)
        name = name.strip()[:MAX_NAME_LENGTH]
        if name:
            self.device_names[key] = name
        else:
            self.device_names.pop(key, None)  # un nom vide efface le nom choisi

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        """Lit les réglages. Un fichier absent ou abîmé donne les valeurs par défaut, jamais une erreur."""
        target = path or settings_file()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        poll = raw.get("poll_seconds", 10)
        names = raw.get("device_names", {})
        clean_names: dict[str, str] = {}
        if isinstance(names, dict):
            for mac, name in names.items():
                try:
                    clean_names[normalize_mac(str(mac))] = str(name)[:MAX_NAME_LENGTH]
                except ValueError:
                    continue
        return cls(
            auto_restart=raw.get("auto_restart") is True,
            poll_seconds=min(max(poll, MIN_POLL_SECONDS), MAX_POLL_SECONDS) if isinstance(poll, int) else 10,
            device_names=clean_names,
        )

    def save(self, path: Path | None = None) -> None:
        target = path or settings_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        payload = {
            "auto_restart": self.auto_restart,
            "poll_seconds": self.poll_seconds,
            "device_names": self.device_names,
        }
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
