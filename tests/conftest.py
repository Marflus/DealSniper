import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dealsniper.config import load_config  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Base SQLite temporaire, aucun canal de notification réel, pas d'attente."""
    monkeypatch.setenv("DEALSNIPER_DATA_DIR", str(tmp_path / "data"))
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("dealsniper.notifier.time.sleep", lambda s: None)


@pytest.fixture
def config():
    cfg = load_config()
    cfg["network"]["detail_delay"] = [0, 0]
    return cfg
