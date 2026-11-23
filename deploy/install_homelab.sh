#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${SUDO_USER:-$(whoami)}"

echo "[1/5] Installation des paquets systeme (python3-venv, xvfb)..."
sudo apt-get update -qq
sudo apt-get install -y python3-venv python3-pip xvfb xauth

echo "[2/5] Creation de l'environnement virtuel et dependances..."
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt

echo "[3/5] Installation des navigateurs Playwright / Patchright..."
.venv/bin/playwright install chromium || true
sudo .venv/bin/playwright install-deps chromium || true
if .venv/bin/python -c "import patchright" 2>/dev/null; then
    .venv/bin/patchright install chrome || .venv/bin/patchright install chromium || true
fi

echo "[4/5] Activation de headless: false pour profiter de Xvfb..."
if [ -f "$APP_DIR/config.yaml" ]; then
    .venv/bin/python - <<'PY'
from pathlib import Path
from ruamel.yaml import YAML

cfg = Path("config.yaml")
yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 4096
doc = yaml.load(cfg.read_text(encoding="utf-8")) or {}
for name in ("leboncoin", "vinted", "facebook"):
    site = (doc.get("sites") or {}).get(name)
    if isinstance(site, dict):
        site["headless"] = False
with cfg.open("w", encoding="utf-8") as fh:
    yaml.dump(doc, fh)
print("headless: false applique pour leboncoin, vinted et facebook.")
PY
fi

echo "[5/5] Installation et demarrage des services systemd..."
for svc in dealsniper.service dealsniper-ui.service; do
    sed -e "s|__USER__|$APP_USER|g" -e "s|__DIR__|$APP_DIR|g" "$APP_DIR/deploy/$svc" \
        | sudo tee "/etc/systemd/system/$svc" >/dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable --now dealsniper.service dealsniper-ui.service

# L'interface écoute sur le réseau local : les autres appareils ont besoin du jeton d'accès (créé au démarrage).
IP="$(hostname -I | awk '{print $1}')"
TOKEN=""
for _ in $(seq 1 15); do
    [ -f "$APP_DIR/data/ui_token" ] && TOKEN="$(tr -d '[:space:]' < "$APP_DIR/data/ui_token")" && break
    sleep 1
done

echo ""
echo "Installation terminee !"
echo "  Logs veille : journalctl -u dealsniper -f"
echo "  Logs UI     : journalctl -u dealsniper-ui -f"
if [ -n "$TOKEN" ]; then
    echo "  Interface   : http://$IP:5000/?token=$TOKEN   (a ouvrir une fois par appareil)"
else
    echo "  Interface   : http://$IP:5000  (jeton : voir journalctl -u dealsniper-ui ou data/ui_token)"
fi
