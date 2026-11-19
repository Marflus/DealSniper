import asyncio

import httpx
import pytest

from dealsniper import engine
from dealsniper.analyzer import AnalysisResult
from dealsniper.config import data_dir
from dealsniper.db import Database
from dealsniper.http import AccessDenied, PoliteClient
from dealsniper.models import Ad
from dealsniper.sites.base import Site


async def no_sleep(_):
    return None


def client(handler=lambda r: httpx.Response(200)):
    return PoliteClient("TestBot/1.0", 0, max_retries=2, transport=httpx.MockTransport(handler), sleep=no_sleep)


# --- client HTTP ---------------------------------------------------------------
def test_http_429_retried_403_refused_robots_respected():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /prive")
        if request.url.path == "/lent" and calls.count("/lent") == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        if request.url.path == "/refus":
            return httpx.Response(403)
        return httpx.Response(200, text="ok")

    async def go():
        async with client(handler) as http:
            assert (await http.get("https://s.fr/lent")).text == "ok"
            with pytest.raises(AccessDenied):
                await http.get("https://s.fr/refus")
            with pytest.raises(AccessDenied):
                await http.get("https://s.fr/prive/1", check_robots=True)

    asyncio.run(go())
    assert calls.count("/lent") == 2 and calls.count("/refus") == 1  # 429 réessayé, 403 jamais


# --- moteur : détection en deux temps ---------------------------------------------
class FakeSite(Site):
    name = "fake"

    def __init__(self, config, refuse=False):
        super().__init__({}, config)
        self.details, self.refuse = [], refuse

    async def list_recent(self, http):
        if self.refuse:
            raise AccessDenied("HTTP 403")
        return [Ad(title="ThinkPad T480 sans SSD", url="https://www.ebay.fr/itm/100000000001", source="ebay", price=90),
                Ad(title="Canapé cuir", url="https://www.ebay.fr/itm/100000000002", source="ebay", price=50),
                Ad(title="Dell Latitude 7490", url="https://www.ebay.fr/itm/100000000003", source="ebay", price=80)]

    async def fetch_detail(self, http, ad):
        self.details.append(ad.uid)
        if "Latitude" in ad.title:
            ad.description = "Carte mère oxydée"
        return ad


def fake_analyze(ads, config, budget):
    for ad in ads:
        ad.analysis = {"type_affaire": "reparation", "score_rentabilite": 8, "confiance": "haute",
                       "marge_nette_estimee": 80}
    return AnalysisResult(analyzed=list(ads), cost_usd=0.001)


def test_two_step_processing(monkeypatch, config):
    monkeypatch.setattr(engine.analyzer, "analyze", fake_analyze)

    async def go():
        db = Database(data_dir() / "t.db")
        site = FakeSite(config)
        async with client() as http:
            eng = engine.Engine(config, db, http)
            first, second = await eng.process(site), await eng.process(site)
        recap = db.analyzed_since(24)
        db.close()
        return site, first, second, recap

    site, first, second, recap = asyncio.run(go())
    # Canapé : rejeté sur la liste, aucune requête de détail. Latitude : rejeté après lecture du détail.
    assert site.details == ["ebay:100000000001", "ebay:100000000003"]
    assert first == {"listées": 3, "nouvelles": 3, "retenues": 1, "analysées": 1, "notifiées": 1}
    assert second["nouvelles"] == 0
    assert [ad.title for ad in recap] == ["ThinkPad T480 sans SSD"]


def test_refused_site_is_paused(monkeypatch, config):
    alerts = []
    monkeypatch.setattr(engine.notifier.Notifier, "send_text", lambda self, text: alerts.append(text) or True)

    async def go():
        db = Database(data_dir() / "p.db")
        async with client() as http:
            eng = engine.Engine(config, db, http)
            await eng.process_safely(FakeSite(config, refuse=True))
            paused = await db.paused_until("fake")
        db.close()
        return paused

    assert asyncio.run(go()) is not None and "refuse l'accès" in alerts[0]


def test_daily_cost_tracking():
    async def go():
        db = Database(data_dir() / "c.db")
        await db.add_cost(0.01)
        await db.add_cost(0.02)
        total = await db.cost_today()
        db.close()
        return total

    assert round(asyncio.run(go()), 3) == 0.03
