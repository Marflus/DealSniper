"""Configuration : `config.yaml` (réglages) + `.env` / variables d'environnement (secrets)."""

from __future__ import annotations

import contextlib
import copy
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent

# Valeurs par défaut : config.yaml ne précise que ce qui change.
DEFAULTS: dict[str, Any] = {
    # Un bloc par site. `interval` = pause (secondes, tirée au hasard) entre deux passages en mode `watch`.
    "sites": {
        "ebay": {"enabled": True, "interval": [600, 900], "marketplace": "EBAY_FR", "country": "FR",
                 "limit": 100, "searches": []},
    },
    # Comportement réseau, commun à tous les sites.
    "network": {
        "user_agent": "DealSniper/3.0 (veille personnelle non commerciale)",
        "min_host_interval": 10,         # secondes minimum entre deux requêtes vers un même site
        "host_intervals": {"api.ebay.com": 1},  # exceptions : l'API officielle eBay est limitée par quota
        "detail_delay": [2, 6],          # pause avant chaque lecture de fiche détaillée
        "access_denied_pause_hours": 6,  # un refus (401/403, robots.txt) met le site en pause
    },
    "searches": [],  # recherches communes, lancées sur tous les sites
    "location": {"france_only": True, "exclude_overseas": True, "home": ""},
    "filters": {"drop_uncategorized": True, "keep_unknown_price": True, "min_price": 5,
                "deal_signals": [], "buyer_ad_keywords": [], "global_exclude_keywords": [],
                "sold_title_keywords": ["vendu", "reserve"],
                "sold_phrases": ["deja vendu", "plus disponible", "n'est plus a vendre", "est reserve",
                                 "en cours de vente", "en cours d'achat", "vente en cours", "transaction en cours"]},
    "categories": [],
    "watchlist": [],  # veille : modèles précis repérés en priorité (watch.py)
    "analyzer": {"enabled": True, "model": "claude-haiku-5-5", "effort": None, "batch_size": 20,
                 "max_description_chars": 400,
                 "max_tokens": 4096, "max_daily_cost_usd": 1.00, "resale_haircut": 0.9, "auto_calibrate": True,
                 "deep_model": "claude-sonnet-5-5", "market_min_margin": 15, "max_retries": 4, "timeout_seconds": 90,
                 "structured_output": True, "pricing": {}},
    "notifier": {"min_score": 7, "watch_score": 5, "instant_min_score": 8, "instant_min_margin": None,
                 "deal_types": ["reparation", "revente_directe"], "auction_reminder_minutes": 10,
                 "min_margin": None, "min_ease": None, "min_sale": None},
    "storage": {"seen_retention_days": 90},
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def config_file() -> Path:
    """config.yaml personnel (ignoré par git), créé depuis config.example.yaml s'il n'existe pas encore."""
    if os.getenv("DEALSNIPER_CONFIG"):
        return Path(os.environ["DEALSNIPER_CONFIG"])
    path = PROJECT_DIR / "config.yaml"
    example = PROJECT_DIR / "config.example.yaml"
    if not path.exists() and example.exists():
        shutil.copyfile(example, path)
    return path


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path) if path else config_file()
    with open(path, encoding="utf-8") as fh:
        return _merge(DEFAULTS, yaml.safe_load(fh) or {})


def data_dir() -> Path:
    """Dossier de la base SQLite (`DEALSNIPER_DATA_DIR` pour les tests)."""
    return Path(os.getenv("DEALSNIPER_DATA_DIR") or PROJECT_DIR / "data")


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_DIR / ".env")


def env(name: str, default: str | None = None) -> str | None:
    """Variable d'environnement (une valeur vide compte comme absente)."""
    return os.getenv(name) or default


# Clés acceptées depuis l'interface (écrites dans .env, jamais renvoyées ni journalisées).
SECRET_KEYS = ("ANTHROPIC_API_KEY", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET",
               "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL")


def write_secrets(values: dict[str, str]) -> list[str]:
    """Écrit/actualise des clés dans le .env local (en gardant le reste) ; renvoie les noms modifiés.

    Le fichier .env est ignoré par git : les valeurs restent sur cette machine et ne servent qu'à
    appeler les API correspondantes. Les valeurs ne sont jamais renvoyées ni écrites dans les logs.
    """
    path = PROJECT_DIR / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated: list[str] = []
    for key, raw in (values or {}).items():
        if key not in SECRET_KEYS:
            continue
        value = str(raw or "").splitlines()[0].strip() if raw else ""  # une seule ligne, pas d'injection
        if not value:
            continue
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                break
        else:
            lines.append(f"{key}={value}")
        os.environ[key] = value  # prise en compte immédiate, sans redémarrer
        updated.append(key)
    if updated:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with contextlib.suppress(OSError):
            path.chmod(0o600)  # lisible par vous seul
    return updated


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
