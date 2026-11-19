"""Configuration : `config.yaml` (réglages) + `.env` / variables d'environnement (secrets)."""

from __future__ import annotations

import copy
import logging
import os
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
        "detail_delay": [2, 6],          # pause avant chaque lecture de fiche détaillée
        "access_denied_pause_hours": 6,  # un refus (401/403, robots.txt) met le site en pause
    },
    "location": {"france_only": True, "exclude_overseas": True},
    "filters": {"drop_uncategorized": True, "keep_unknown_price": True, "min_price": 5,
                "deal_signals": [], "buyer_ad_keywords": [], "global_exclude_keywords": []},
    "categories": [],
    "analyzer": {"model": "claude-haiku-4-5", "effort": None, "batch_size": 20, "max_description_chars": 400,
                 "max_tokens": 4096, "max_daily_cost_usd": 1.00, "max_retries": 4, "timeout_seconds": 90,
                 "structured_output": True, "pricing": {}},
    "notifier": {"min_score": 7, "watch_score": 5, "deal_types": ["reparation", "revente_directe"]},
    "storage": {"seen_retention_days": 90},
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path or os.getenv("DEALSNIPER_CONFIG") or PROJECT_DIR / "config.yaml")
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


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
