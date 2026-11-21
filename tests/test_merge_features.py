"""Fonctions ajoutées par Marius : message de négociation, rentabilité horaire, sauvegarde / restauration."""

import io
import zipfile

from dealsniper import purchases
from dealsniper.deep import build_negotiation_message, sanitize
from dealsniper.models import Ad


def test_negotiation_message_uses_advised_price():
    ad = Ad(title="ThinkPad T480", url="https://x/1", source="ebay", price=150,
            analysis={"panne_probable": "SSD absent"})
    msg = build_negotiation_message(ad, 120)
    assert "120 €" in msg and "ssd absent" in msg
    assert "€" not in build_negotiation_message(ad, 200)  # prix déjà sous le conseillé : simple prise de contact
    deep = sanitize({"revente_typique": 250, "revente_basse": 180}, ad)  # 250 × 0,9 − 75 = 150
    assert deep["prix_achat_max"] == 150 and "150 €" not in deep["message_negociation"]  # 150 = prix demandé
    deep = sanitize({"revente_typique": 200, "message_negociation": "Bonjour, 110 € ?"}, ad)
    assert deep["message_negociation"] == "Bonjour, 110 € ?"  # message de l'IA gardé tel quel


def test_hourly_rate():
    sold = {"buy_price": 60, "repair_cost": 20, "sold_price": 180, "hours_spent": 4}
    assert purchases.hourly_rate(sold) == 25.0
    assert purchases.hourly_rate({**sold, "hours_spent": None}) is None
    assert purchases.hourly_rate({**sold, "sold_price": None}) is None
    s = purchases.summary([sold, {"buy_price": 50, "sold_price": 90}])  # 2e vente sans temps : hors €/h
    assert s["hourly_rate"] == 25.0 and s["sold_hours"] == 4


def test_negotiation_endpoint(client):
    c, _ = client
    data = c.get("/api/ads/ebay:100000000001/negotiation").get_json()
    assert data["ok"] and data["message"].startswith("Bonjour")
    assert c.get("/api/ads/inconnue/negotiation").status_code == 404


def test_purchase_hours_roundtrip(client):
    c, _ = client
    pid = c.post("/api/purchases", json={"title": "Dyson V8", "buy_price": "40", "hours_spent": "1.5"}).get_json()["id"]
    c.put(f"/api/purchases/{pid}", json={"sold_price": "100"})
    row = next(p for p in c.get("/api/purchases").get_json()["purchases"] if p["id"] == pid)
    assert row["hours_spent"] == 1.5 and row["hourly_rate"] == 40.0


def test_backup_and_restore(client):
    c, _ = client
    c.post("/api/ads/ebay:100000000001/mark", json={"favorite": True})
    archive = c.get("/api/backup")
    assert archive.status_code == 200
    names = set(zipfile.ZipFile(io.BytesIO(archive.data)).namelist())
    assert names == {"config.yaml", "dealsniper.db"}  # jamais .env
    c.post("/api/ads/ebay:100000000001/mark", json={"favorite": False})
    resp = c.post("/api/restore", data={"file": (io.BytesIO(archive.data), "save.zip")},
                  content_type="multipart/form-data")
    assert resp.get_json()["ok"] and set(resp.get_json()["restored"]) == {"config.yaml", "dealsniper.db"}
    ad = next(a for a in c.get("/api/ads").get_json()["ads"] if a["uid"] == "ebay:100000000001")
    assert ad["favorite"] is True  # état de la sauvegarde retrouvé
    bad = c.post("/api/restore", data={"file": (io.BytesIO(b"pas un zip"), "x.zip")},
                 content_type="multipart/form-data")
    assert bad.status_code == 400


def test_negotiation_title_is_cut_on_a_word():
    from dealsniper.deep import clip

    title = "Nintendo Switch OLED étui transport, coque tablette et Joy-Con, écran de verre et abonnement 7 jours"
    cut = clip(title, 80)
    assert cut.endswith("…") and len(cut) <= 81 and "verre e…" not in cut
    assert clip("court", 80) == "court"
