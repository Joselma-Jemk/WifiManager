# WifiManager

Pilote le hotspot mobile de Windows : mot de passe en un clic, appareils connectés, accès temporaires et blocage,
relance automatique. Voir [DESIGN.md](DESIGN.md) pour la conception et l'état d'avancement.

## Développement

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests --select E,F,W,I --ignore E501
```

Les tests n'ont pas besoin de Windows : PowerShell et la table ARP sont remplacés par des exécuteurs factices.

## Contact

contact@joselma.online
