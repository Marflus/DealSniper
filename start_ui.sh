#!/usr/bin/env sh
# Deal Sniper : lance l'interface web. Crée l'environnement Python au premier lancement.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "Premier lancement : création de l'environnement Python..."
    python3 -m venv .venv
fi

echo "Vérification des dépendances..."
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
exec .venv/bin/python -m dealsniper ui --open "$@"
