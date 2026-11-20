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
    assert a["valeur_revente_estimee"] == 180  # 200 € rabotés de 10 %
    assert a["marge_nette_estimee"] == 180 - 90 - 20 - 18  # revente - prix - pièce - 10 % de frais


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
    assert p["buy_price"] == 90 and p["estimated_resale"] == 180 and p["model"] == "thinkpad t480"
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
