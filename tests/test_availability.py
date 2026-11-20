import asyncio
import threading
import time

import httpx

from dealsniper.availability import ACTIVE, GONE, UNKNOWN, check_one
from dealsniper.db import Database
from dealsniper.http import PoliteClient
from dealsniper.models import Ad


async def _no_sleep(_):
    return None


def test_check_one_by_site():
    def handler(request):
        path = request.url.path
        if "redirige" in path:
            return httpx.Response(302, headers={"Location": "https://www.vinted.fr/catalog"})
        if "terminee" in path:
            return httpx.Response(200, text="<h1>Cette annonce est terminée</h1>")
        return httpx.Response(404 if "supprimee" in path else 403 if "bloque" in path else 200, text="ok")

    async def go():
        async with PoliteClient("test", 0, transport=httpx.MockTransport(handler), sleep=_no_sleep) as http:
            return [
                await check_one(http, None, {"source": "vinted", "url": "https://www.vinted.fr/items/1-ok"}),
                await check_one(http, None, {"source": "vinted", "url": "https://www.vinted.fr/items/2-supprimee"}),
                await check_one(http, None, {"source": "vinted", "url": "https://www.vinted.fr/items/3-bloque"}),
                await check_one(http, None, {"source": "leboncoin", "url": "https://www.leboncoin.fr/ad/x/1"}),
                await check_one(http, None, {"source": "ebay", "url": "https://www.ebay.fr/itm/123-terminee"}),
                await check_one(http, None, {"source": "vinted", "url": "https://www.vinted.fr/items/4-redirige"}),
            ]

    results = asyncio.run(go())
    assert [r[0] for r in results] == [ACTIVE, GONE, UNKNOWN, UNKNOWN, GONE, GONE]
    assert "anti-robots" in results[3][1]  # leboncoin : jamais interrogé


def test_availability_saved_and_endpoint(client, monkeypatch, tmp_path):
    db = Database(tmp_path / "a.db")
    ad = Ad(title="Steam Deck", url="https://www.vinted.fr/items/9", source="vinted", price=200)
    asyncio.run(db.remember(ad, "analyzed"))
    db.set_availability({ad.uid: GONE, "inconnue": UNKNOWN}, "2026-11-20T10:00:00+00:00")
    assert db.ad_data(ad.uid)["gone"] is True and db.ad_data(ad.uid)["checked_at"].startswith("2026-11-20")
    db.close()

    c, _ = client

    async def fake_check(ads, config, on_progress=None, stop=None):
        return {"total": len(ads), "done": len(ads), "active": 0, "unknown": [], "cancelled": False,
                "gone": [{"uid": a["uid"], "title": a["title"]} for a in ads],
                "statuses": {a["uid"]: GONE for a in ads}, "checked_at": "2026-11-20T10:00:00+00:00"}

    monkeypatch.setattr("dealsniper.availability.check_ads", fake_check)
    assert c.post("/api/check", json={"uids": []}).status_code == 400
    assert c.post("/api/check", json={"uids": ["ebay:100000000001"]}).get_json()["ok"]
    deadline = time.monotonic() + 10  # time.sleep est neutralisé dans les tests : attente par horloge
    while (state := c.get("/api/check").get_json())["running"] and time.monotonic() < deadline:
        threading.Event().wait(0.05)
    assert len(state["report"]["gone"]) == 1, state
    gone = [a for a in c.get("/api/ads").get_json()["ads"] if a["uid"] == "ebay:100000000001"]
    assert gone and gone[0]["gone"] is True
    assert c.post("/api/ads/hide", json={"uids": ["ebay:100000000001"]}).get_json()["count"] == 1
