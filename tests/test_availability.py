import asyncio
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
    deadline = time.monotonic() + 10
    while (state := c.get("/api/check").get_json())["running"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(state["report"]["gone"]) == 1, state
    gone = [a for a in c.get("/api/ads").get_json()["ads"] if a["uid"] == "ebay:100000000001"]
    assert gone and gone[0]["gone"] is True
    assert c.post("/api/ads/hide", json={"uids": ["ebay:100000000001"]}).get_json()["count"] == 1


def test_run_refused_while_checking(client, monkeypatch):
    import threading

    c, _ = client
    release = threading.Event()

    async def slow_check(ads, config, on_progress=None, stop=None):
        release.wait(5)
        return {"total": 0, "done": 0, "active": 0, "gone": [], "unknown": [], "cancelled": False,
                "statuses": {}, "checked_at": "2026-11-20T10:00:00+00:00"}

    monkeypatch.setattr("dealsniper.availability.check_ads", slow_check)
    assert c.post("/api/check", json={"uids": ["ebay:100000000001"]}).get_json()["ok"]
    try:
        assert c.post("/api/run").status_code == 409
        assert c.post("/api/search-now", json={"query": "ski"}).status_code == 409
    finally:
        release.set()
    deadline = time.monotonic() + 10
    while c.get("/api/check").get_json()["running"] and time.monotonic() < deadline:
        time.sleep(0.05)


def test_check_ads_report_groups_sites_and_stops(config):
    """Bilan complet : en ligne / disparues / non vérifiables, progression, arrêt demandé en cours de route."""
    from dealsniper.availability import check_ads

    def handler(request):
        return httpx.Response(404 if request.url.path.endswith("-gone") else 200, text="ok")

    ads = [{"uid": f"v{i}", "title": f"Vinted {i}", "source": "vinted",
            "url": f"https://www.vinted.fr/items/{i}" + ("-gone" if i == 1 else "")} for i in range(3)]
    ads.append({"uid": "lbc", "title": "LBC", "source": "leboncoin", "url": "https://www.leboncoin.fr/ad/x/9"})
    seen = []

    async def go(stop=None):
        async with PoliteClient("test", 0, transport=httpx.MockTransport(handler), sleep=_no_sleep) as http:
            return await check_ads(ads, config, on_progress=lambda r: seen.append(r["done"]), stop=stop, http=http)

    report = asyncio.run(go())
    assert report["total"] == 4 and report["done"] == 4 and report["active"] == 2
    assert [g["uid"] for g in report["gone"]] == ["v1"] and [u["uid"] for u in report["unknown"]] == ["lbc"]
    assert report["statuses"] == {"v0": ACTIVE, "v1": GONE, "v2": ACTIVE, "lbc": UNKNOWN}
    assert sorted(seen) == [1, 2, 3, 4] and not report["cancelled"]

    calls = []
    report = asyncio.run(go(stop=lambda: calls.append(1) or len(calls) > 2))  # arrêt après deux annonces
    assert report["cancelled"] and report["done"] < 4


def test_ebay_checked_through_the_api(config, monkeypatch):
    """Avec la clé eBay : l'API dit si l'objet est terminé (404 ou plus disponible)."""
    from dealsniper.sites.ebay import EbaySite

    def handler(request):
        if "oauth2" in request.url.path:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
        item = request.url.params["legacy_item_id"]
        if item == "111111111111":
            return httpx.Response(404)
        status = "OUT_OF_STOCK" if item == "222222222222" else "IN_STOCK"
        return httpx.Response(200, json={"estimatedAvailabilities": [{"estimatedAvailabilityStatus": status}]})

    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    ebay = EbaySite({"marketplace": "EBAY_FR", **config["sites"]["ebay"]}, config)

    async def go():
        async with PoliteClient("test", 0, transport=httpx.MockTransport(handler), sleep=_no_sleep) as http:
            return [(await check_one(http, ebay, {"source": "ebay", "url": f"https://www.ebay.fr/itm/{n}"}))[0]
                    for n in ("111111111111", "222222222222", "333333333333")]

    assert asyncio.run(go()) == [GONE, GONE, ACTIVE]


def test_site_that_limits_is_not_waited_for(config):
    """429 / 403 : on n'attend pas (pas de longue pause) et on n'insiste pas : le reste du site passe
    en « non vérifiable », les autres sites continuent ; le restant et la durée estimée sont suivis."""
    from dealsniper.availability import check_ads

    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "www.vinted.fr":
            return httpx.Response(429, headers={"Retry-After": "900"})
        return httpx.Response(200, text="ok")

    ads = [{"uid": f"v{i}", "title": f"V{i}", "source": "vinted", "url": f"https://www.vinted.fr/items/{i}"}
           for i in range(5)]
    ads += [{"uid": f"e{i}", "title": f"E{i}", "source": "ebay", "url": f"https://www.ebay.fr/itm/12345678901{i}"}
            for i in range(2)]
    snapshots = []

    async def go():
        async with PoliteClient("test", 0, max_retries=0, transport=httpx.MockTransport(handler),
                                sleep=_no_sleep) as http:
            return await check_ads(ads, config, http=http,
                                   on_progress=lambda r: snapshots.append((dict(r["pending"]), r["eta_s"])))

    report = asyncio.run(go())
    assert calls.count("www.vinted.fr") == 1  # une seule tentative sur le site qui limite
    assert report["done"] == 7 and report["active"] == 2
    vinted = [u for u in report["unknown"] if u["source"] == "vinted"]
    assert len(vinted) == 5 and all("limite" in u["note"] for u in vinted)
    assert snapshots and snapshots[-1][0] == {"vinted": 0, "ebay": 0} and snapshots[-1][1] == 0
