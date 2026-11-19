import asyncio
import shutil

import pytest

from dealsniper.config import PROJECT_DIR, data_dir, load_config
from dealsniper.db import Database
from dealsniper.models import Ad
from dealsniper.web.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch, config):
    # Copie d'un config.yaml valide, modifiable sans toucher au vrai fichier.
    import os

    source = os.getenv("DEALSNIPER_CONFIG") or PROJECT_DIR / "config.yaml"
    path = tmp_path / "config.yaml"
    shutil.copy(source, path)
    monkeypatch.setenv("DEALSNIPER_CONFIG", str(path))

    async def seed():
        db = Database(data_dir() / "dealsniper.db")
        good = Ad(title="ThinkPad T480 sans SSD", url="https://www.ebay.fr/itm/100000000001", source="ebay", price=90,
                  category="PC portables pros", analysis={"score_rentabilite": 8, "marge_nette_estimee": 80})
        bad = Ad(title="Canapé", url="https://www.ebay.fr/itm/100000000002", source="ebay", price=50)
        await db.remember(good, "analyzed", notified=True)
        await db.remember(bad, "rejected", reason="hors catégorie")
        db.close()

    asyncio.run(seed())
    return create_app().test_client(), path


def test_ads_listing_and_status_filter(client):
    c, _ = client
    assert c.get("/").status_code == 200
    data = c.get("/api/ads?days=7").get_json()
    assert {a["title"] for a in data["ads"]} == {"ThinkPad T480 sans SSD", "Canapé"}
    rejected = c.get("/api/ads?status=rejected").get_json()["ads"]
    assert [a["reason"] for a in rejected] == ["hors catégorie"]


def test_settings_roundtrip_keeps_comments(client):
    c, path = client
    settings = c.get("/api/settings").get_json()
    settings["sites"]["ebay"]["searches"] = [{"query": "ps5", "condition": "parts", "max_price": "150"},
                                             {"query": "", "max_price": "10"}]  # ligne vide ignorée
    settings["categories"][0]["max_budget"] = "200"
    settings["filters"]["global_exclude_keywords"] = "oxyde, icloud ,"
    settings["notifier"]["min_score"] = "8"
    assert c.put("/api/settings", json=settings).get_json() == {"ok": True}

    cfg = load_config(path)
    assert cfg["sites"]["ebay"]["searches"] == [{"query": "ps5", "condition": "parts", "max_price": 150}]
    assert cfg["categories"][0]["max_budget"] == 200
    assert cfg["filters"]["global_exclude_keywords"] == ["oxyde", "icloud"]
    assert cfg["notifier"]["min_score"] == 8
    assert "# ---" in path.read_text(encoding="utf-8")  # commentaires conservés


def test_invalid_config_is_reported(client):
    c, path = client
    path.write_text("sites:\n  ebay:\n    enabled: true\n      searches: x\n", encoding="utf-8")
    resp = c.get("/api/settings")
    assert resp.status_code == 500 and "illisible" in resp.get_json()["error"]
