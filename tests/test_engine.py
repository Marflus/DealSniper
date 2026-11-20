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
        ad.analysis = {"type_affaire": "reparation", "score_rentabilite": 8, "facilite_reparation": 9,
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


def test_missing_dependency_does_not_block_other_sites(monkeypatch, config):
    from dealsniper import sites

    monkeypatch.setitem(sites.SITE_MODULES, "absent", "module_inexistant:Classe")
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    cfg = {**config, "sites": {"absent": {"enabled": True}, "ebay": config["sites"]["ebay"]}}
    assert [s.name for s in sites.load_sites(cfg)] == ["ebay"]


def test_common_searches_are_merged_per_site():
    from dealsniper.sites import merge_searches

    common = [{"query": "ps5", "condition": "parts", "max_price": 200},
              {"query": "PS5", "condition": "used", "max_price": 260}, {"query": "dyson", "max_price": 80}]
    own = [{"query": "tv pas d'image", "category": "16", "max_price": 120}]
    # eBay filtre l'état : les deux recherches ps5 restent distinctes.
    assert len(merge_searches(common, own, uses_condition=True)) == 4
    # Ailleurs : une seule recherche ps5, avec le prix max le plus large, sans `condition`.
    merged = merge_searches(common, own, uses_condition=False)
    assert merged[0] == {"query": "PS5", "max_price": 260} and len(merged) == 3


def test_progress_keeps_search_log_and_summary(capsys):
    from dealsniper.progress import Progress

    p = Progress()
    p.update("eBay", "recherche…")
    p.log_search("eBay", "thinkpad", 25)
    p.log_search("eBay", "dyson", 0)
    p.add_cost("eBay", 0.01)
    p.done("eBay", "25 annonces")
    snap = p.snapshot()
    assert snap["done"] and snap["cost"] == 0.01
    assert snap["sites"][0]["searches"] == [{"query": "thinkpad", "found": 25}, {"query": "dyson", "found": 0}]
    p.print_summary()
    out = capsys.readouterr().out
    assert "eBay (2 recherches, 25 annonces)" in out and "thinkpad 25 · dyson 0" in out


def test_personal_config_is_created_from_example(tmp_path, monkeypatch):
    from dealsniper import config as cfg

    (tmp_path / "config.example.yaml").write_text("analyzer: {batch_size: 7}\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "PROJECT_DIR", tmp_path)
    monkeypatch.delenv("DEALSNIPER_CONFIG", raising=False)
    assert cfg.load_config()["analyzer"]["batch_size"] == 7
    assert (tmp_path / "config.yaml").exists()  # copie personnelle, modifiable sans toucher à l'exemple


def test_leboncoin_image_formats():
    import sys
    import types

    if "patchright" not in sys.modules:  # le connecteur importe patchright ; inutile pour lire le JSON
        try:
            import patchright.async_api  # noqa: F401
        except ImportError:
            fake = types.ModuleType("patchright.async_api")
            fake.TimeoutError, fake.async_playwright = TimeoutError, None
            sys.modules["patchright"] = types.ModuleType("patchright")
            sys.modules["patchright.async_api"] = fake
    from dealsniper.sites.leboncoin import ad_image

    assert ad_image({"images": {"urls_large": ["https://img.leboncoin.fr/a.jpg"]}}) == "https://img.leboncoin.fr/a.jpg"
    assert ad_image({"images": {"thumb_url": "https://img.leboncoin.fr/t.jpg"}}) == "https://img.leboncoin.fr/t.jpg"
    assert ad_image({"images": [{"url": "https://img.leboncoin.fr/l.jpg"}]}) == "https://img.leboncoin.fr/l.jpg"
    assert ad_image({"pictures": {"x": ["https://img.leboncoin.fr/api/v1/p.jpg"]}}) == "https://img.leboncoin.fr/api/v1/p.jpg"
    assert ad_image({"images": {"nb_images": 0}}) is None


def test_known_ads_get_their_missing_image(tmp_path):
    import asyncio

    from dealsniper.db import Database
    from dealsniper.models import Ad

    async def scenario():
        db = Database(tmp_path / "t.db")
        ad = Ad(title="PS5", url="https://www.leboncoin.fr/ad/consoles/1", source="leboncoin", price=100)
        await db.remember(ad, "analyzed")
        await db.fill_images({ad.uid: "https://img.leboncoin.fr/1.jpg"})
        image = db.ad_data(ad.uid)["image"]
        db.close()
        return image

    assert asyncio.run(scenario()) == "https://img.leboncoin.fr/1.jpg"


def test_only_sites_disables_the_others():
    from dealsniper.cli import only_sites

    config = {"sites": {"ebay": {"enabled": True}, "vinted": {"enabled": True}}}
    only_sites(config, "ebay")
    assert config["sites"]["ebay"]["enabled"] and not config["sites"]["vinted"]["enabled"]


def test_market_rejects_impossible_margins_before_ai(config):
    import asyncio

    from dealsniper.engine import Engine
    from dealsniper.market import Quote
    from dealsniper.models import Ad

    class FakeMarket:
        brands = ["thinkpad"]

        async def quote(self, ad):
            return Quote("thinkpad t480", 200, 12, "annonces")

    engine = Engine(config, db=None, http=None, market=FakeMarket())
    cheap = Ad(title="ThinkPad T480", url="https://e.x/1", source="t", price=60)
    dear = Ad(title="ThinkPad T480", url="https://e.x/2", source="t", price=160)
    assert asyncio.run(engine.market_problem(cheap)) is None and cheap.market["median"] == 200
    assert asyncio.run(engine.market_problem(dear)).startswith("marge impossible selon la cote")


def test_duplicates_and_favorites_and_purchases(tmp_path):
    import asyncio

    from dealsniper.db import Database
    from dealsniper.market import fingerprint
    from dealsniper.models import Ad
    from dealsniper.purchases import calibration, summary

    brands = ["thinkpad"]
    lbc = Ad(title="Lenovo ThinkPad T480 i5", url="https://www.leboncoin.fr/ad/1", source="leboncoin", price=120)
    fb = Ad(title="Thinkpad T480 - très bon état", url="https://www.facebook.com/marketplace/item/9/",
            source="facebook", price=120)
    assert fingerprint(lbc, brands) == fingerprint(fb, brands) == "thinkpad t480|120"

    async def scenario():
        db = Database(tmp_path / "d.db")
        await db.remember(lbc, "analyzed", fingerprint=fingerprint(lbc, brands))
        same_site = await db.duplicate_of("thinkpad t480|120", "leboncoin")
        other_site = await db.duplicate_of("thinkpad t480|120", "facebook")
        assert db.mark(lbc.uid, favorite=True) and not db.mark("inconnue", hidden=True)
        await db.remember(lbc, "analyzed", fingerprint="x")  # un nouveau passage ne retire pas le favori
        favorite = db.search()[0]["favorite"]
        pid = db.add_purchase({"uid": lbc.uid, "title": lbc.title, "buy_price": 120, "estimated_resale": 200,
                               "model": "thinkpad t480"})
        db.update_purchase(pid, {"repair_cost": 20, "sold_price": 180})
        db.record_sale(pid, "thinkpad t480", 180)
        prices = await db.working_prices("thinkpad t480", 30)
        rows = db.purchases()
        db.close()
        return same_site, other_site, favorite, rows, prices

    same_site, other_site, favorite, rows, prices = asyncio.run(scenario())
    assert same_site is None and other_site == "leboncoin" and favorite
    assert summary(rows)["profit"] == 40 and summary(rows)["roi_pct"] == 28.6 and prices == [180]
    assert calibration(rows, 0.9)["haircut"] is None  # moins de 3 ventes : pas de calibrage
    three = [{"sold_price": 90, "estimated_resale": 100}] * 3
    assert calibration(three, 0.9)["haircut"] == 0.81


def test_demo_seed_populates_once(tmp_path):
    from dealsniper.db import Database
    from dealsniper.demo import seed

    db = Database(tmp_path / "demo.db")
    seed(db)
    seed(db)  # relancer n'ajoute rien
    ads = db.search()
    assert len(ads) == len(__import__("dealsniper.demo", fromlist=["DEMO_ADS"]).DEMO_ADS)
    assert sum(a["favorite"] for a in ads) == 2
    assert all(a["image"] == "" for a in ads)  # pas de photo = aucune requête d'aperçu
    assert len(db.purchases()) == 3
    db.close()


def test_no_ai_mode_scores_from_cote(monkeypatch, config, tmp_path):
    import asyncio

    from dealsniper.db import Database
    from dealsniper.engine import Engine
    from dealsniper.market import Quote
    from dealsniper.models import Ad

    config = {**config, "analyzer": {**config["analyzer"], "enabled": False}}

    class FakeMarket:
        brands = ["thinkpad"]

        async def observe(self, ads): pass

        async def quote(self, ad):
            return Quote("thinkpad t480", 220, 12, "annonces")

    class FakeSite:
        name, label, cfg = "ebay", "eBay", {"searches": [], "fetch_details": False}
        on_progress = None

        def report(self, *a): pass

        async def list_recent(self, http):
            return [Ad(title="ThinkPad T480 sans SSD", url="https://www.ebay.fr/itm/1", source="ebay", price=90)]

        async def fetch_detail(self, http, ad):
            return ad

    async def run():
        db = Database(tmp_path / "noai.db")
        engine = Engine(config, db, http=None, market=FakeMarket())
        notified = []
        monkeypatch.setattr("dealsniper.notifier.notify", lambda ads, cfg: notified.extend(ads) or list(ads))
        stats = await engine.process(FakeSite())
        rows = db.search(status="analyzed")
        db.close()
        return stats, rows

    stats, rows = asyncio.run(run())
    assert stats["analysées"] == 1 and rows and rows[0]["analysis"]["sans_ia"] is True
    assert rows[0]["analysis"]["valeur_revente_estimee"] == 198  # 220 × 0,9, sans aucun appel IA
