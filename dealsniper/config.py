"""Chargement de la configuration (`config.yaml`) et des secrets (`.env` / variables d'environnement)."""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.yaml"

# Valeurs par défaut : config.yaml n'a besoin de préciser que ce qui change.
DEFAULTS: dict[str, Any] = {
    "sources": {
        "email": {
            "enabled": True,
            "folder": "INBOX",
            "lookback_days": 3,
            "max_emails": None,
            "mark_as_read": False,
            "senders": {},
            "ad_url_patterns": [],
            "resolve_tracking_links": True,
        },
        "rss": {"enabled": True, "timeout": 20, "feeds": []},
    },
    "location": {"france_only": True, "exclude_overseas": True},
    "filters": {
        "drop_uncategorized": True,
        "keep_unknown_price": True,
        "min_price": 5,
        "deal_signals": [],
        "buyer_ad_keywords": [],
        "global_exclude_keywords": [],
    },
    "categories": [],
    "analyzer": {
        "model": "claude-haiku-4-5",
        "effort": None,
        "batch_size": 10,
        "max_description_chars": 600,
        "max_tokens": 4096,
        "max_ads_per_run": None,
        "max_daily_cost_usd": 1.00,
        "max_retries": 4,
        "timeout_seconds": 90,
        "structured_output": True,
        "pricing": {},
    },
    "notifier": {
        "min_score": 7,
        "max_per_run": None,
        "deal_types": ["reparation", "revente_directe"],
        "health_alerts": True,
    },
    "storage": {"seen_retention_days": 60, "max_deals_history": 500},
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Lit `config.yaml` (ou `DEALSNIPER_CONFIG`) et complète avec les valeurs par défaut."""
    path = Path(path or os.getenv("DEALSNIPER_CONFIG") or DEFAULT_CONFIG_PATH)
    with open(path, encoding="utf-8") as fh:
        user_cfg = yaml.safe_load(fh) or {}
    # Compatibilité : l'ancienne clé `sources.imap` s'appelle désormais `sources.email`.
    sources = user_cfg.get("sources") or {}
    if "imap" in sources and "email" not in sources:
        sources["email"] = sources.pop("imap")
    return _deep_merge(DEFAULTS, user_cfg)


def data_dir() -> Path:
    """Dossier des fichiers d'état (surchargeable via `DEALSNIPER_DATA_DIR`, utile pour les tests)."""
    return Path(os.getenv("DEALSNIPER_DATA_DIR") or PROJECT_DIR / "data")


def load_env() -> None:
    """Charge `.env` s'il existe. En CI, les variables viennent des secrets GitHub."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_DIR / ".env")


def env(name: str, default: str | None = None) -> str | None:
    """Variable d'environnement, en traitant une valeur vide comme absente."""
    value = os.getenv(name)
    return value if value else default


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
