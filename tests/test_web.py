from dealsniper.config import load_config


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
    settings["searches"] = [{"query": "dyson", "condition": "parts", "max_price": "80"}]
    assert c.put("/api/settings", json=settings).get_json() == {"ok": True}

    cfg = load_config(path)
    assert cfg["sites"]["ebay"]["searches"] == [{"query": "ps5", "condition": "parts", "max_price": 150}]
    assert cfg["categories"][0]["max_budget"] == 200
    assert cfg["filters"]["global_exclude_keywords"] == ["oxyde", "icloud"]
    assert cfg["notifier"]["min_score"] == 8
    assert cfg["searches"] == [{"query": "dyson", "condition": "parts", "max_price": 80}]
    assert "# ---" in path.read_text(encoding="utf-8")  # commentaires conservés


def test_new_settings_are_saved(client):
    c, path = client
    settings = c.get("/api/settings").get_json()
    settings["notifier"].update(min_margin="40", min_ease="", watch_score="")
    settings["filters"]["keep_unknown_price"] = False
    settings["analyzer"].update(batch_size="25", max_description_chars="300")
    settings["storage"] = {"seen_retention_days": "30"}
    settings["network"] = {"access_denied_pause_hours": "12"}
    assert c.put("/api/settings", json=settings).get_json() == {"ok": True}

    cfg = load_config(path)
    assert cfg["notifier"]["min_margin"] == 40 and cfg["notifier"]["min_ease"] is None
    assert cfg["notifier"]["watch_score"] is None
    assert cfg["filters"]["keep_unknown_price"] is False
    assert (cfg["analyzer"]["batch_size"], cfg["analyzer"]["max_description_chars"]) == (25, 300)
    assert cfg["storage"]["seen_retention_days"] == 30
    assert cfg["network"]["access_denied_pause_hours"] == 12


def test_invalid_config_is_reported(client):
    c, path = client
    path.write_text("sites:\n  ebay:\n    enabled: true\n      searches: x\n", encoding="utf-8")
    resp = c.get("/api/settings")
    assert resp.status_code == 500 and "illisible" in resp.get_json()["error"]


def test_og_image_parsing():
    from dealsniper.web.preview import og_image

    html = '<head><meta property="og:title" content="x"><meta content="/img/1.jpg" property="og:image"></head>'
    assert og_image(html, "https://www.vinted.fr/items/1") == "https://www.vinted.fr/img/1.jpg"
    facebook_logo = '<meta property="og:image" content="https://static.xx.fbcdn.net/rsrc.php/logo.png">'
    assert og_image(facebook_logo, "https://f.x") is None


def test_image_endpoint_fetches_once(client, monkeypatch):
    c, _ = client
    calls = []
    monkeypatch.setattr("dealsniper.web.preview.fetch_image", lambda url: calls.append(url) or "https://img.x/1.jpg")
    uid = next(a["uid"] for a in c.get("/api/ads").get_json()["ads"] if a["title"] == "Canapé")
    assert c.get(f"/api/image/{uid}").get_json() == {"image": "https://img.x/1.jpg"}
    assert c.get(f"/api/image/{uid}").get_json() == {"image": "https://img.x/1.jpg"}
    assert len(calls) == 1  # mémorisé en base


def test_old_analyses_get_the_realism_guard(client):
    c, _ = client
    ad = next(a for a in c.get("/api/ads").get_json()["ads"] if a["title"] == "ThinkPad T480 sans SSD")
    a = ad["analysis"]
    assert a["realiste"] and "facilite_vente" not in a  # ancienne analyse : pas de note inventée
    assert a["valeur_revente_estimee"] == 198  # 200 € remontés au repère du T480 (220 €), rabotés de 10 %
    assert a["marge_nette_estimee"] == 68.2  # 198 − 90 − 20 − 19,80 : revente - prix - pièce - 10 % de frais


def test_reset_config_from_example(client):
    c, path = client
    example = path.read_text(encoding="utf-8").replace("min_score: 7", "min_score: 9")
    (path.parent / "config.example.yaml").write_text(example, encoding="utf-8")
    data = c.post("/api/settings/reset").get_json()
    assert data["ok"] and data["backup"].startswith("config.")
    assert (path.parent / data["backup"]).exists()  # ancienne configuration gardée
    assert load_config(path)["notifier"]["min_score"] == 9


def test_marks_and_purchases_api(client):
    c, _ = client
    uid = next(a["uid"] for a in c.get("/api/ads").get_json()["ads"] if a["title"] == "ThinkPad T480 sans SSD")
    assert c.post(f"/api/ads/{uid}/mark", json={"favorite": True}).get_json()["ok"]
    assert c.post("/api/ads/inconnue/mark", json={"hidden": True}).status_code == 404
    ad = next(a for a in c.get("/api/ads").get_json()["ads"] if a["uid"] == uid)
    assert ad["favorite"] and not ad["hidden"]

    created = c.post("/api/purchases", json={"uid": uid}).get_json()
    assert created["ok"]
    data = c.get("/api/purchases").get_json()
    p = data["purchases"][0]
    assert p["buy_price"] == 90 and p["estimated_resale"] == 198 and p["model"] == "thinkpad t480"
    assert c.put(f"/api/purchases/{p['id']}", json={"sold_price": "200", "repair_cost": "25"}).get_json()["ok"]
    data = c.get("/api/purchases").get_json()
    assert data["purchases"][0]["sold_at"] and data["summary"]["profit"] == 85
    assert data["calibration"]["haircut"] is None and data["calibration"]["sales"] == 1
    assert c.post("/api/purchases", json={"title": "", "buy_price": 10}).status_code == 400
    c.delete(f"/api/purchases/{p['id']}")
    assert c.get("/api/purchases").get_json()["purchases"] == []


def test_market_analysis_endpoint(client, monkeypatch):
    c, _ = client
    captured = {}

    def fake(ad, config):
        captured["title"] = ad.title
        return {"revente_basse": 170, "revente_typique": 195, "revente_haute": 220, "confiance": "élevée",
                "sources": ["eBay vendus : 180-210 €"], "resume": "Modèle recherché.", "pieges": "vérifier l'écran",
                "prix_achat_max": 120, "cost_usd": 0.02, "model": "claude-sonnet-5-5",
                "ventes": [{"date": "2026-02", "prix": 190, "source": "ebay"}]}

    monkeypatch.setattr("dealsniper.deep.analyze_market", fake)
    uid = next(a["uid"] for a in c.get("/api/ads").get_json()["ads"] if a["title"] == "ThinkPad T480 sans SSD")
    data = c.post(f"/api/ads/{uid}/market-analysis").get_json()
    assert data["ok"] and data["market_deep"]["revente_typique"] == 195 and captured["title"]
    # Résultat mémorisé sur l'annonce et coût ajouté.
    ad = next(a for a in c.get("/api/ads").get_json()["ads"] if a["uid"] == uid)
    assert ad["market_deep"]["confiance"] == "élevée" and ad["market_deep"]["ventes"][0]["prix"] == 190
    assert c.post("/api/ads/inconnue/market-analysis").status_code == 404


def test_secrets_written_to_env(client, tmp_path, monkeypatch):
    c, _ = client
    import dealsniper.config as cfg
    monkeypatch.setattr(cfg, "PROJECT_DIR", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    body = {"ANTHROPIC_API_KEY": "sk-ant-test123", "INCONNU": "x", "EBAY_CLIENT_ID": ""}
    data = c.post("/api/secrets", json=body).get_json()
    assert data["ok"] and data["updated"] == ["ANTHROPIC_API_KEY"] and data["anthropic"]
    env_text = (tmp_path / ".env").read_text()
    assert "ANTHROPIC_API_KEY=sk-ant-test123" in env_text and "INCONNU" not in env_text  # seules les clés connues


def test_watchlist_roundtrip(client):
    c, path = client
    settings = c.get("/api/settings").get_json()
    settings["watchlist"] = [{"query": "thinkpad t480", "max_price": "120"}, {"query": "", "max_price": "50"}]
    assert c.put("/api/settings", json=settings).get_json() == {"ok": True}
    cfg = load_config(path)
    assert cfg["watchlist"] == [{"query": "thinkpad t480", "max_price": 120}]  # ligne vide ignorée


def test_preferences_from_favorites_and_hidden(client):
    c, _ = client
    ads = c.get("/api/ads").get_json()["ads"]
    fav = next(a["uid"] for a in ads if a["title"] == "ThinkPad T480 sans SSD")  # catégorie PC portables pros
    c.post(f"/api/ads/{fav}/mark", json={"favorite": True})
    prefs = c.get("/api/preferences").get_json()["categories"]
    assert prefs.get("PC portables pros") == 1


def test_site_health_records_and_clears_error(tmp_path):
    import asyncio

    from dealsniper.db import Database

    async def scenario():
        db = Database(tmp_path / "health.db")
        await db.record_site_ok("ebay", 42)
        await db.pause("vinted", 6, "DataDome")
        before = db.site_health()
        await db.record_site_ok("vinted", 25)  # une récolte réussie efface l'erreur
        after = db.site_health()
        db.close()
        return before, after

    before, after = asyncio.run(scenario())
    assert before["ebay"]["last_count"] == 42 and before["vinted"]["last_error"] == "DataDome"
    assert after["vinted"]["last_error"] is None and after["vinted"]["last_count"] == 25


def test_site_unpause(client):
    import asyncio

    from dealsniper.config import data_dir
    from dealsniper.db import Database

    c, _ = client
    with Database(data_dir() / "dealsniper.db") as db:
        asyncio.run(db.pause("leboncoin", 6, "DataDome"))
    status = c.get("/api/status").get_json()
    lbc = next(s for s in status["sites"] if s["name"] == "leboncoin")
    assert lbc["paused_until"] is not None
    assert c.post("/api/sites/leboncoin/unpause").get_json() == {"ok": True}
    status = c.get("/api/status").get_json()
    lbc = next(s for s in status["sites"] if s["name"] == "leboncoin")
    assert lbc["paused_until"] is None


def test_max_ads_per_run_saved(client):
    c, path = client
    settings = c.get("/api/settings").get_json()
    settings["analyzer"]["max_ads_per_run"] = "25"
    assert c.put("/api/settings", json=settings).get_json() == {"ok": True}
    assert load_config(path)["analyzer"]["max_ads_per_run"] == 25


def test_search_now_validates_and_starts(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr("dealsniper.engine.run_once", lambda config: None)
    assert c.post("/api/search-now", json={"query": ""}).status_code == 400
    assert c.post("/api/search-now", json={"query": "thinkpad x1", "max_price": "150"}).get_json()["ok"]


def test_search_now_applies_options(client, monkeypatch):
    import time

    c, _ = client
    seen = []
    monkeypatch.setattr("dealsniper.engine.run_once", lambda config: seen.append(config))
    body = {"query": "raspberry pi", "min_price": "20", "max_price": "80", "condition": "used",
            "exclude": "boîtier seul, alim", "max_ads": "15", "sites": ["ebay"]}
    assert c.post("/api/search-now", json=body).get_json()["ok"]
    start = time.time()
    while not seen and time.time() - start < 10:
        time.sleep(0.05)
    config = seen[0]
    assert config["searches"] == [{"query": "raspberry pi", "condition": "used", "max_price": 80, "min_price": 20}]
    assert config["filters"]["min_price"] == 20 and "boitier seul" in config["filters"]["global_exclude_keywords"]
    assert config["analyzer"]["max_ads_per_run"] == 15
    assert all(s.get("enabled") is False for name, s in config["sites"].items() if name != "ebay")


def test_seller_questions_on_demand(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    class Resp:
        text = '{"questions": ["Le BIOS est-il verrouillé ?", "Chargeur fourni ?"]}'
        content = [type("B", (), {"type": "text", "text": text})()]
        usage = type("U", (), {"input_tokens": 300, "output_tokens": 60})()

    class FakeClient:
        class messages:  # noqa: N801
            @staticmethod
            def create(**params):
                assert "tools" not in params  # pas de recherche web : appel léger
                return Resp()

    import dealsniper.deep as deep
    original = deep.seller_questions
    monkeypatch.setattr(deep, "seller_questions", lambda ad, cfg, d=None: original(ad, cfg, d, client=FakeClient()))
    data = c.post("/api/ads/ebay:100000000001/questions").get_json()
    assert data["ok"] and data["questions"][0].startswith("Le BIOS")
    ad = [a for a in c.get("/api/ads").get_json()["ads"] if a["uid"] == "ebay:100000000001"][0]
    assert ad["market_deep"]["questions"] == data["questions"]


def test_progress_endpoint_is_light(client):
    c, _ = client
    data = c.get("/api/progress").get_json()
    assert set(data) == {"running", "error", "progress", "cost_today", "cost_cap"}  # pas la liste des annonces


def test_missing_photo_is_retried_later_except_blocked_sites(client, monkeypatch, tmp_path):
    from dealsniper.config import data_dir
    from dealsniper.db import Database

    c, _ = client
    calls = []
    monkeypatch.setattr("dealsniper.web.preview.fetch_image", lambda url: calls.append(url) or None)
    uid = next(a["uid"] for a in c.get("/api/ads").get_json()["ads"] if a["title"] == "Canapé")
    assert c.get(f"/api/image/{uid}").get_json() == {"image": None} and len(calls) == 1
    c.get(f"/api/image/{uid}")
    assert len(calls) == 1  # échec récent : pas de nouvel essai tout de suite
    ad = next(a for a in c.get("/api/ads").get_json()["ads"] if a["uid"] == uid)
    assert ad["image"] == ""  # l'interface ne la redemande pas
    with Database(data_dir() / "dealsniper.db") as db:  # 8 jours plus tard…
        db.patch_ad(uid, {"image_checked_at": "2000-01-01T00:00:00+00:00"})
    ad = next(a for a in c.get("/api/ads").get_json()["ads"] if a["uid"] == uid)
    assert ad["image"] is None  # à redemander
    c.get(f"/api/image/{uid}")
    assert len(calls) == 2
    with Database(data_dir() / "dealsniper.db") as db:  # leboncoin : bloque ces aperçus, on n'insiste pas
        db.patch_ad(uid, {"source": "leboncoin", "image_checked_at": "2000-01-01T00:00:00+00:00"})
    c.get(f"/api/image/{uid}")
    assert len(calls) == 2


def test_favorites_view_ignores_the_period(client):
    from dealsniper.config import data_dir
    from dealsniper.db import Database

    c, _ = client
    c.post("/api/ads/ebay:100000000001/mark", json={"favorite": True})
    with Database(data_dir() / "dealsniper.db") as db:  # favori vieux de 6 mois
        db._sql("UPDATE seen SET seen_at = '2026-05-01T00:00:00+00:00' WHERE uid = 'ebay:100000000001'")
    assert not [a for a in c.get("/api/ads?days=7").get_json()["ads"] if a["favorite"]]
    favs = c.get("/api/ads?days=3650&only=favorite").get_json()["ads"]
    assert [a["uid"] for a in favs] == ["ebay:100000000001"]


def test_demo_mode_never_reaches_the_outside(client):
    """Mode démo : pas de passage, de recherche, de vérification, de notification ni d'IA (fausses données)."""
    from dealsniper.web.app import create_app

    demo = create_app(demo=True).test_client()
    for path in ("/api/run", "/api/search-now", "/api/check", "/api/test-notif", "/api/secrets",
                 "/api/ads/ebay:100000000001/market-analysis", "/api/ads/ebay:100000000001/questions"):
        resp = demo.post(path, json={"query": "x", "uids": ["ebay:100000000001"]})
        assert resp.status_code == 409 and "démo" in resp.get_json()["error"], path
    assert demo.get("/api/status").get_json()["demo"] is True
    assert demo.post("/api/ads/ebay:100000000001/mark", json={"favorite": True}).status_code == 200  # local : ok


def test_stats_use_the_same_margins_as_the_list(client, monkeypatch):
    """Le Bilan applique les mêmes corrections que la liste (meilleure marge +859 € au lieu de +698 €)."""
    from dealsniper.db import Database

    seen = []
    original = Database.stats

    def spy(self, days=30, prepare=None):
        seen.append(prepare)
        return original(self, days, prepare)

    monkeypatch.setattr(Database, "stats", spy)
    c, _ = client
    assert c.get("/api/stats").status_code == 200
    assert seen and seen[0] is not None


def test_notifications_switch_and_silent_one_off_search(client, monkeypatch, config):
    from dealsniper import notifier
    from dealsniper.config import load_config

    c, path = client
    settings = c.get("/api/settings").get_json()
    settings["notifier"]["enabled"] = False
    assert c.put("/api/settings", json=settings).get_json() == {"ok": True}
    assert load_config(path)["notifier"]["enabled"] is False

    config["notifier"]["enabled"] = False
    monkeypatch.setattr(notifier.Notifier, "send_ad", lambda *a: (_ for _ in ()).throw(AssertionError("envoi")))
    assert notifier.notify([], config) == []
    assert notifier.enabled({"notifier": {}})  # absent = activé (anciennes configurations)


def test_search_results_say_which_search_they_belong_to(client):
    """Après un rechargement, l'interface réaffiche le compte rendu de la dernière recherche ponctuelle."""
    c, _ = client
    data = c.get("/api/search-results").get_json()
    assert data["query"] is None and data["running"] is False  # aucune recherche lancée
