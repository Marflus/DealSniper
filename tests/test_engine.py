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


def fake_analyze(ads, config, budget, should_stop=None):
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


def test_tax_overview_thresholds():
    from dealsniper.purchases import tax_overview

    assert tax_overview([])["level"] == "ok"
    many = [{"sold_price": 100, "sold_at": "2026-02-01", "buy_price": 50, "repair_cost": 0}] * 31
    over = tax_overview(many, year=2026)
    assert over["level"] == "depasse" and over["sales"] == 31 and over["revenue"] == 3100
    # Ventes d'une autre année ignorées.
    assert tax_overview([{"sold_price": 100, "sold_at": "2020-01-01"}], year=2026)["sales"] == 0


def test_watched_ad_is_always_notified(monkeypatch, config):
    from dealsniper.models import Ad
    from dealsniper.notifier import notify

    monkeypatch.setattr("dealsniper.notifier.Notifier.send_ad", lambda self, ad: True)
    monkeypatch.setattr("dealsniper.notifier._pause", lambda s: None)
    cfg = {**config, "notifier": {**config["notifier"], "min_score": 9, "watch_score": None}}
    low = Ad(title="ThinkPad T480", url="https://e.x/1", source="t", price=90,
             analysis={"score_rentabilite": 3, "type_affaire": "reparation"})
    low.watched = True  # une veille passe malgré une note faible
    assert notify([low], cfg) == [low]


def test_ending_soon_selects_unreminded_auctions(tmp_path):
    import asyncio
    from datetime import UTC, datetime, timedelta

    from dealsniper.db import Database
    from dealsniper.models import Ad
    from dealsniper.notifier import auction_reminder

    soon = (datetime.now(UTC) + timedelta(minutes=8)).isoformat()
    late = (datetime.now(UTC) + timedelta(hours=5)).isoformat()

    async def scenario():
        db = Database(tmp_path / "a.db")
        a = Ad(title="PS5 enchère", url="https://www.ebay.fr/itm/1", source="ebay", price=120,
               price_mode="enchere", ends_at=soon, analysis={"score_rentabilite": 8})
        b = Ad(title="PS5 plus tard", url="https://www.ebay.fr/itm/2", source="ebay", price=120,
               price_mode="enchere", ends_at=late, analysis={"score_rentabilite": 8})
        fixe = Ad(title="PS5 prix fixe", url="https://www.ebay.fr/itm/3", source="ebay", price=120,
                  analysis={"score_rentabilite": 8})
        for ad in (a, b, fixe):
            await db.remember(ad, "analyzed")
        first = [x.uid for x in db.ending_soon(15)]
        db.mark_reminded(a.uid)
        second = db.ending_soon(15)
        msg = auction_reminder(a)
        db.close()
        return first, second, msg

    first, second, msg = asyncio.run(scenario())
    assert len(first) == 1 and second == []  # seule l'enchère proche, une seule fois
    assert "Enchère bientôt finie" in msg and "PS5 enchère" in msg


def test_price_drop_detection(tmp_path):
    import asyncio

    from dealsniper.db import Database
    from dealsniper.models import Ad

    async def scenario():
        db = Database(tmp_path / "drop.db")
        ad = Ad(title="ThinkPad T480", url="https://www.ebay.fr/itm/1", source="ebay", price=120, shipping=0,
                analysis={"score_rentabilite": 8})
        await db.remember(ad, "analyzed")
        big = db.record_price_drops({ad.uid: 90.0}, 10)   # −30 € : signalé
        small = db.record_price_drops({ad.uid: 85.0}, 10)  # −5 € depuis 90 : ignoré
        stored = db.ad_data(ad.uid)
        db.close()
        return big, small, stored

    big, small, stored = asyncio.run(scenario())
    assert big and big[0]["pct"] == 25 and big[0]["to"] == 90
    assert small == [] and stored["price"] == 90 and stored["price_drop"]["from"] == 120


def test_max_ads_per_run_caps_candidates(monkeypatch, config):
    import asyncio

    from dealsniper.db import Database
    from dealsniper.engine import Engine
    from dealsniper.models import Ad

    cfg = {**config, "analyzer": {**config["analyzer"], "max_ads_per_run": 2}}

    class FakeSite:
        name, label, cfg = "ebay", "eBay", {"searches": [], "fetch_details": False}
        on_progress = None

        def report(self, *a): pass

        async def list_recent(self, http):
            return [Ad(title=f"ThinkPad {i}", url=f"https://www.ebay.fr/itm/{i}", source="ebay", price=50,
                       category="PC portables pros") for i in range(6)]

        async def fetch_detail(self, http, ad): return ad

    sent = []

    async def run():
        import tempfile
        db = Database(__import__("pathlib").Path(tempfile.mkdtemp()) / "cap.db")
        engine = Engine(cfg, db, http=None)
        def analyze(ads, c, b=None, s=None):
            sent.append(len(ads))
            return AnalysisResult(analyzed=list(ads))

        monkeypatch.setattr("dealsniper.analyzer.analyze", analyze)
        monkeypatch.setattr("dealsniper.notifier.notify", lambda ads, c: [])
        stats = await engine.process(FakeSite())
        db.close()
        return stats

    stats = asyncio.run(run())
    assert sent == [2] and stats["retenues"] == 2  # seulement 2 envoyées à l'IA


def test_watched_ad_survives_detail_and_drop_ignores_shipping(monkeypatch, config):
    """Une veille n'est pas écartée par la fiche détaillée ; une baisse se mesure sur le prix, port à part."""
    monkeypatch.setattr(engine.analyzer, "analyze", fake_analyze)
    cfg = {**config, "watchlist": [{"query": "latitude 7490"}]}

    class ShippedSite(FakeSite):
        price = 80

        async def list_recent(self, http):
            ads = await super().list_recent(http)
            for ad in ads:
                ad.shipping, ad.price = 8, (self.price if "Latitude" in ad.title else ad.price)
            return ads

    async def go():
        db = Database(data_dir() / "w.db")
        site = ShippedSite(cfg)
        async with client() as http:
            eng = engine.Engine(cfg, db, http)
            first = await eng.process(site)
            site.price = 75  # −5 € : sous le seuil de 10 €, même avec 8 € de port
            await eng.process(site)
            unchanged = db.ad_data("ebay:100000000003")["price"]
            site.price = 60  # −20 €
            await eng.process(site)
            dropped = db.ad_data("ebay:100000000003")
        db.close()
        return first, unchanged, dropped

    first, unchanged, dropped = asyncio.run(go())
    assert first["retenues"] == 2  # ThinkPad + Latitude surveillé malgré « carte mère oxydée »
    assert unchanged == 80
    assert dropped["price"] == 60 and dropped["total_price"] == 68 and dropped["price_drop"]["from"] == 80


def test_rank_tolerates_missing_margin():
    from dealsniper.notifier import _rank

    a = Ad(title="a", url="https://x/1", source="t", analysis={"score_rentabilite": 7, "marge_nette_estimee": None})
    b = Ad(title="b", url="https://x/2", source="t", analysis={"score_rentabilite": 7, "marge_nette_estimee": 30})
    assert [x.title for x in _rank([a, b])] == ["b", "a"]


def test_cancel_stops_before_saving(monkeypatch, config):
    """Annulation : la liste est lue, mais rien n'est enregistré ni envoyé à l'IA (tout revient ensuite)."""
    calls = []
    monkeypatch.setattr(engine.analyzer, "analyze", lambda *a: calls.append(a))

    async def go():
        db = Database(data_dir() / "c.db")
        async with client() as http:
            eng = engine.Engine(config, db, http)
            eng.stopping.set()
            stats = await eng.process(FakeSite(config))
        unseen = await db.unseen(["ebay:100000000001"])
        db.close()
        return stats, unseen

    stats, unseen = asyncio.run(go())
    assert stats["listées"] == 3 and stats["analysées"] == 0 and not calls and unseen == {"ebay:100000000001"}


def test_zone_is_eliminatory(config):
    cfg = {**config, "filters": {**config["filters"], "zone": {"near": "Lyon", "km": 30}}}
    eng = engine.Engine(cfg, db=None, http=None)

    class FakeGeo:
        def distance(self, center, place):
            return {"Villeurbanne": 5, "Marseille": 280}.get(place)

    eng._geo, eng._center = FakeGeo(), (45.76, 4.83)
    near = Ad(title="a", url="https://x/1", source="t", location="Villeurbanne")
    far = Ad(title="b", url="https://x/2", source="t", location="Marseille")
    unknown = Ad(title="c", url="https://x/3", source="t")
    assert asyncio.run(eng.zone_problem(near)) is None
    assert "hors zone (280 km" in asyncio.run(eng.zone_problem(far))
    assert asyncio.run(eng.zone_problem(unknown)) is None  # ville inconnue : gardée


def test_one_off_search_rechecks_rejected_and_reports(monkeypatch, config):
    """Recherche ponctuelle : une annonce écartée par d'anciens critères est réévaluée ; RUN_UIDS liste tout."""
    monkeypatch.setattr(engine.analyzer, "analyze", fake_analyze)

    async def go():
        db = Database(data_dir() / "r.db")
        async with client() as http:
            first = await engine.Engine(config, db, http).process(FakeSite(config))
            cfg = {**config, "recheck_rejected": True,
                   "filters": {**config["filters"], "drop_uncategorized": False}}
            second = await engine.Engine(cfg, db, http).process(FakeSite(cfg))
        db.close()
        return first, second

    engine.RUN_UIDS.clear()
    first, second = asyncio.run(go())
    assert first["nouvelles"] == 3 and second["nouvelles"] == 2  # canapé + Latitude réévalués, ThinkPad déjà noté
    assert len(engine.RUN_UIDS) == 3


def test_no_hidden_budget_in_one_off_search(config):
    from dealsniper.filters import check

    cfg = {**config, "filters": {**config["filters"], "drop_uncategorized": False,
                                 "ignore_category_budget": True, "default_max_budget": None}}
    ski = Ad(title="Ski Rossignol 170 cm", url="https://x/ski", source="vinted", price=180)
    assert check(ski, cfg) is None and ski.category == "Autre"


def test_ai_failures_are_given_up_after_three_tries(monkeypatch, config):
    """Une annonce que l'IA ne note jamais n'est pas repayée indéfiniment : abandonnée au 3e échec."""
    calls = []

    def always_fails(ads, cfg, budget, should_stop=None):
        calls.append(len(ads))
        return AnalysisResult(failed=list(ads))

    monkeypatch.setattr(engine.analyzer, "analyze", always_fails)

    async def go():
        db = Database(data_dir() / "f.db")
        async with client() as http:
            eng = engine.Engine(config, db, http)
            for _ in range(4):
                await eng.process(FakeSite(config))
        seen = db.search(days=1)
        db.close()
        return seen

    seen = asyncio.run(go())
    assert calls == [1, 1, 1]  # 3 essais pour le ThinkPad, puis plus aucun appel
    thinkpad = next(a for a in seen if a["uid"] == "ebay:100000000001")
    assert thinkpad["status"] == "rejected" and "3 essais" in thinkpad["reason"]


def test_cancel_interrupts_between_searches(config):
    """Annuler pendant la liste : le site s'arrête après la recherche en cours, sans attendre les suivantes."""
    class ManySearches(FakeSite):
        async def list_recent(self, http):
            for i in range(5):
                self.details.append(i)
                if i == 1:
                    eng.stopping.set()  # l'utilisateur clique « Annuler » pendant la 2e recherche
                self.report(f"recherche {i}", 3)
            return []

    async def go():
        nonlocal eng
        db = Database(data_dir() / "x.db")
        async with client() as http:
            eng = engine.Engine(config, db, http)
            stats = await eng.process(site)
        db.close()
        return stats

    eng = None
    site = ManySearches(config)
    stats = asyncio.run(go())
    assert site.details == [0, 1] and stats["listées"] == 6 and stats["analysées"] == 0


def test_cap_applies_before_reading_details(monkeypatch, config):
    """Plafond « IA max » : seules les annonces analysées voient leur fiche lue (pas de requêtes pour rien)."""
    monkeypatch.setattr(engine.analyzer, "analyze", fake_analyze)
    cfg = {**config, "analyzer": {**config["analyzer"], "max_ads_per_run": 2}}

    class SixThinkPads(FakeSite):
        async def list_recent(self, http):
            return [Ad(title=f"ThinkPad T480 sans SSD n°{i}", url=f"https://www.ebay.fr/itm/20000000000{i}",
                       source="ebay", price=60 + i) for i in range(6)]

    async def go():
        db = Database(data_dir() / "cap2.db")
        site = SixThinkPads(cfg)
        async with client() as http:
            stats = await engine.Engine(cfg, db, http).process(site)
            unseen = await db.unseen([f"ebay:20000000000{i}" for i in range(6)])
        db.close()
        return site, stats, unseen

    site, stats, unseen = asyncio.run(go())
    assert len(site.details) == 2 and stats["analysées"] == 2
    assert site.details == ["ebay:200000000000", "ebay:200000000001"]  # les moins chères d'abord
    assert len(unseen) == 4  # les 4 autres reviendront au prochain passage


def test_price_drop_updates_margin_and_score(monkeypatch, config):
    """Baisse de prix d'une annonce notée : marge et note recalculées, alerte avec la nouvelle marge."""
    alerts = []
    monkeypatch.setattr(engine.notifier, "notify_price_drops", alerts.extend)
    price = {"value": 170}

    class OneAd(FakeSite):
        async def list_recent(self, http):
            return [Ad(title="ThinkPad T480 sans SSD", url="https://www.ebay.fr/itm/300000000001", source="ebay",
                       price=price["value"])]

    def analyze(ads, cfg, budget, should_stop=None):
        for ad in ads:
            raw = {"type_affaire": "reparation", "score_rentabilite": 8, "facilite_reparation": 9,
                   "facilite_vente": 8, "panne_probable": "SSD", "cout_piece_estime": 20,
                   "valeur_revente_estimee": 2000 / 9, "marge_nette_estimee": 0}
            ad.analysis = engine.analyzer.make_realistic(raw, ad)
        return AnalysisResult(analyzed=list(ads))

    monkeypatch.setattr(engine.analyzer, "analyze", analyze)

    async def go():
        db = Database(data_dir() / "drop2.db")
        async with client() as http:
            eng = engine.Engine(config, db, http)
            await eng.process(OneAd(config))
            before = db.ad_data("ebay:300000000001")["analysis"]
            price["value"] = 110
            await eng.process(OneAd(config))
        after = db.search(days=1)[0]
        db.close()
        return before, after

    before, after = asyncio.run(go())
    assert before["score_rentabilite"] == 4  # marge négative à 170 €
    assert after["analysis"]["marge_nette_estimee"] == 50 and after["score"] == 8
    assert alerts and alerts[0]["margin"] == 50 and alerts[0]["score"] == 8


def test_missing_browser_gives_a_clear_hint(config):
    from dealsniper.engine import missing_browser_hint

    assert "playwright install chromium" in missing_browser_hint(
        Exception("BrowserType.launch: Executable doesn't exist at /ms-playwright/chromium … playwright install"))
    assert "patchright install chromium" in missing_browser_hint(Exception("run: patchright install chromium"))
    assert missing_browser_hint(Exception("Timeout 30000ms exceeded")) is None

    class NoBrowser(FakeSite):
        async def list_recent(self, http):
            raise RuntimeError("Executable doesn't exist at C:/ms-playwright/chromium-1243/chrome.exe")

    async def go():
        db = Database(data_dir() / "nb.db")
        async with client() as http:
            eng = engine.Engine(config, db, http)
            await eng.process_safely(NoBrowser(config))
        db.close()
        return eng.progress.snapshot()

    snap = asyncio.run(go())
    assert "navigateur manquant" in snap["sites"][0]["text"]


def test_only_sites_reason():
    from pathlib import Path

    from dealsniper.cli import only_sites
    from dealsniper.config import load_config
    from dealsniper.sites import load_sites, skipped

    config = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
    only_sites(config, "ebay")
    load_sites(config)
    others = [reason for name, reason in skipped if name != "ebay"]
    assert others and all(reason == "non retenu (--sites)" for reason in others)


def test_run_once_end_to_end(monkeypatch, config):
    """Passage complet : verrou pris puis rendu, annonces du passage connues, résultat en base, progression finie."""
    from dealsniper import progress as progress_module
    from dealsniper.runlock import run_lock

    monkeypatch.setattr(engine, "load_sites", lambda cfg: [FakeSite(cfg)])
    monkeypatch.setattr(engine, "skipped_sites", [])  # pas de restes des tests précédents
    monkeypatch.setattr(engine.analyzer, "analyze", fake_analyze)
    monkeypatch.setattr(engine.notifier, "notify", lambda ads, cfg: list(ads))
    engine.CANCEL.set()  # annulation restée d'un passage précédent : doit être oubliée au démarrage
    engine.run_once(config)

    run_uids = set(engine.RUN_UIDS)
    assert run_uids == {"ebay:100000000001", "ebay:100000000002", "ebay:100000000003"}
    snap = progress_module.current.snapshot()
    assert snap["done"] and [s["site"] for s in snap["sites"]] == ["Fake"]
    assert "1 analysées" in snap["sites"][0]["text"]
    with Database(data_dir() / "dealsniper.db") as db:
        statuses = {a["uid"]: a["status"] for a in db.search(days=1)}
    assert statuses == {"ebay:100000000001": "analyzed", "ebay:100000000002": "rejected",
                        "ebay:100000000003": "rejected"}
    with run_lock(data_dir()):  # verrou bien rendu à la fin
        pass


def test_watch_mode_stops_cleanly(monkeypatch, config):
    """Surveillance continue : démarre, puis s'arrête proprement à la demande (verrou rendu)."""
    import threading
    import time

    from dealsniper.runlock import Busy, run_lock

    monkeypatch.setattr(engine, "load_sites", lambda cfg: [FakeSite(cfg)])
    monkeypatch.setattr(engine, "skipped_sites", [])
    monkeypatch.setattr(engine.analyzer, "analyze", fake_analyze)
    monkeypatch.setattr(engine.notifier, "notify", lambda ads, cfg: [])
    thread = threading.Thread(target=engine.watch, args=(config,), daemon=True)
    thread.start()
    time.sleep(0.5)  # démarrage
    with pytest.raises(Busy):  # la surveillance tient le verrou : pas de passage en double
        run_lock(data_dir()).acquire()
    engine.CANCEL.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    with run_lock(data_dir()):
        pass


def test_detail_pages_are_capped_per_run(monkeypatch, config):
    """`max_details` : au plus N fiches ouvertes par passage (une page de plus chacune), les autres annonces
    sont notées avec les infos de la liste."""
    monkeypatch.setattr(engine.analyzer, "analyze", fake_analyze)

    async def go():
        db = Database(data_dir() / "cap.db")
        site = FakeSite(config)
        site.cfg["max_details"] = 1
        async with client() as http:
            stats = await engine.Engine(config, db, http).process(site)
        db.close()
        return site, stats

    site, stats = asyncio.run(go())
    assert site.details == ["ebay:100000000001"]  # Latitude non ouverte : pas de « carte mère oxydée » vue
    assert stats["retenues"] == 2


def test_vinted_and_leboncoin_detail_defaults():
    from dealsniper.sites.base import merge_description
    from dealsniper.sites.leboncoin import LeboncoinSite
    from dealsniper.sites.vinted import VintedSite, ld_description

    assert LeboncoinSite.details_default is False and VintedSite.max_details_default == 15
    blocks = ['{"@type":"Product","name":"X390","description":"Quelques lignes de pixels hs sur l\'écran"}']
    assert ld_description(blocks) == "Quelques lignes de pixels hs sur l'écran"
    assert ld_description(["pas du json"]) == ""
    assert merge_description("État : Bon état", "Écran HS") == "État : Bon état | Écran HS"
    assert merge_description("État : Bon état", "") == "État : Bon état"


def test_details_are_read_for_the_best_scored_then_rescored(monkeypatch, config):
    """Vinted : fiche lue APRÈS la notation, pour les mieux notées ; une panne grave dans le texte les écarte."""
    calls = []

    def analyze(ads, cfg, budget, should_stop=None):
        calls.append([a.title for a in ads])
        return fake_analyze(ads, cfg, budget, should_stop)

    monkeypatch.setattr(engine.analyzer, "analyze", analyze)

    async def go():
        db = Database(data_dir() / "after.db")
        site = FakeSite(config)
        site.cfg["max_details"] = 5
        async with client() as http:
            stats = await engine.Engine(config, db, http).process(site)
        rejected = db.search(status="rejected")
        db.close()
        return site, stats, rejected

    site, stats, rejected = asyncio.run(go())
    assert len(calls) == 1  # aucune description nouvelle à relire (T480) ; Latitude écartée par sa fiche
    assert set(site.details) == {"ebay:100000000001", "ebay:100000000003"}
    assert any("oxyde" in (r["reason"] or "") for r in rejected)
    assert stats["analysées"] == 1
