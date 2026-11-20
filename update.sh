#!/usr/bin/env sh
# Deal Sniper : récupère la dernière version (git pull) puis relance l'interface.
set -e
cd "$(dirname "$0")"
pkill -f "dealsniper ui" 2>/dev/null || true   # arrête l'interface si elle tourne
git pull
exec ./start_ui.sh "$@"
