"""Raccourci historique : `python main.py [commande]` = `python -m dealsniper [commande]`."""

import sys

from dealsniper.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
