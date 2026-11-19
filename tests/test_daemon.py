import asyncio

import httpx
import pytest

from dealsniper.analyzer import AnalysisResult
from dealsniper.config import data_dir
from dealsniper.daemon import runner
from dealsniper.daemon.connectors.base import Connector
from dealsniper.daemon.http import AccessDenied, PoliteClient
from dealsniper.daemon.store import Store
from dealsniper.models import Ad


async def no_sleep(_):
    return None


def make_client(handler):
    return PoliteClient("TestBot/1.0", min_host_interval=0, max_retries=2,
                        transport=httpx.MockTransport(handler), sleep=no_sleep)


# --- client HTTP ---------------------------------------------------------------
def test_429_is_retried_then_succeeds():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"}) if len(calls) == 1 else httpx.Response(200, text="ok")

    async def go():
        async with make_client(handler) as http:
            return await http.get("https://api.example.com/x")

    assert asyncio.run(go()).text == "ok" and len(calls) == 2
    assert calls[0].headers["User-Agent"] == "TestBot/1.0"


def test_403_stops_immediately_without_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403)

    async def go():
        async with make_client(handler) as http:
            await http.get("https://site.example.com/page")

    with pytest.raises(AccessDenied):
        asyncio.run(go())
    assert len(calls) == 1


def test_robots_txt_is_respected():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /annonces")
        return httpx.Response(200, text="page")

    async def go(path):
        async with make_client(handler) as http:
            return await http.get(f"https://site.example.com{path}", check_robots=True)

    assert asyncio.run(go("/blog")).text == "page"
    with pytest.raises(AccessDenied):
        asyncio.run(go("/annonces/123"))


# --- base SQLite ----------------------------------------------------------------
def test_store_seen_pause_and_costs():
    async def go():
        store = Store(data_dir() / "t.db")
        ad = Ad(title="ThinkPad", url="https://www.ebay.fr/itm/123456789012", source="ebay", price=80)
        assert await store.unseen([ad.uid, "x"]) == {ad.uid, "x"}
        await store.remember(ad, "analyzed", 8, True)
        assert await store.unseen([ad.uid]) == set()
        assert await store.paused_until("ebay") is None
        await store.pause("ebay", 1, "HTTP 403")
        assert await store.paused_until("ebay") is not None
        await store.add_cost(0.01)
        await store.add_cost(0.02)
        assert round(await store.cost_today(), 3) == 0.03
        store.close()

    asyncio.run(go())


# --- orchestrateur : détection en deux temps --------------------------------------
class FakeConnector(Connector):
    name = "fake"

    def __init__(self, config):
        super().__init__({}, config)
        self.detail_calls = []

    async def list_recent(self, http):
        return [
            Ad(title="ThinkPad T480 sans SSD", url="https://www.ebay.fr/itm/100000000001", source="ebay", price=90),
            Ad(title="Canapé cuir", url="https://www.ebay.fr/itm/100000000002", source="ebay", price=50),
            Ad(title="Dell Latitude 7490", url="https://www.ebay.fr/itm/100000000003", source="ebay", price=80),
        ]

    async def fetch_detail(self, http, ad):
        self.detail_calls.append(ad.uid)
        if "Latitude" in ad.title:
            ad.description = "Carte mère oxydée"
        return ad


def fake_analyze(ads, config, budget):
    for ad in ads:
        ad.analysis = {"type_affaire": "reparation", "score_rentabilite": 8, "confiance": "haute",
                       "marge_nette_estimee": 80}
    return AnalysisResult(analyzed=list(ads), cost_usd=0.001)


def test_two_stage_cycle(monkeypatch, config):
    monkeypatch.setattr(runner.analyzer, "analyze", fake_analyze)
    config = {**config, "daemon": {"detail_delay": [0, 0]}}

    async def go():
        store = Store(data_dir() / "d.db")
        connector = FakeConnector(config)
        async with make_client(lambda r: httpx.Response(200)) as http:
            daemon = runner.Daemon(config, store, http, [connector])
            first = await daemon.run_cycle(connector)
            second = await daemon.run_cycle(connector)
        store.close()
        return connector, first, second

    connector, first, second = asyncio.run(go())
    # Le canapé est rejeté sur la liste (pas de détail) ; le Latitude est rejeté après lecture du détail.
    assert connector.detail_calls == ["ebay:100000000001", "ebay:100000000003"]
    assert first == {"listed": 3, "new": 3, "kept": 1, "analyzed": 1, "notified": 1}
    assert second["new"] == 0  # tout est déjà en base : aucune requête de détail
