"""Script d'entrée pour la compilation Nuitka : même comportement que `python -m wifimanager.app`."""

from wifimanager.app import main

if __name__ == "__main__":
    raise SystemExit(main())
