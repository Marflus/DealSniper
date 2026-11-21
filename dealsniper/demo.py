"""Données de démonstration pour voir l'interface sans rien lancer (python -m dealsniper ui --demo).

Aucun appel réseau : des annonces et des achats fictifs sont écrits dans une base séparée
(data/demo/) pour ne pas toucher à vos vraies données. Les photos sont volontairement absentes
(aucune requête d'aperçu de lien) ; seuls les logos des sites peuvent charger un favicon public.
"""

from __future__ import annotations

from datetime import date, timedelta

from .db import Database
from .models import Ad


def _ad(title, source, price, score, rep, vente, *, type_="reparation", panne="", location="Lyon 69003",
        resale=None, piece=0, category="PC portables pros", market=None, status="analyzed", reason=None):
    analysis = None
    if status == "analyzed":
        analysis = {
            "realiste": True, "type_affaire": type_, "score_rentabilite": score,
            "facilite_reparation": rep, "facilite_vente": vente, "panne_probable": panne or "aucune",
            "cout_piece_estime": piece, "valeur_revente_estimee": resale,
            "valeur_revente_ia": (resale or 0) + 40 if resale else None,
            "source_revente": "cote" if market else "ia",
            "marge_nette_estimee": None if resale is None else round(resale - price - piece - 0.1 * resale),
        }
    ad = Ad(title=title, url=f"https://www.{source}.fr/annonce-demo/{abs(hash(title)) % 100000}", source=source,
            price=price, location=location, category=category, image="", price_mode="fixe",
            market=market, analysis=analysis)
    return ad, status, reason


DEMO_ADS = [
    _ad("Lenovo ThinkPad T480 i5 8 Go — sans SSD", "ebay", 85, 8, 9, 8, panne="SSD absent", resale=200, piece=25,
        location="Lyon 69003", market={"model": "thinkpad t480", "median": 220, "count": 18, "source": "annonces"}),
    _ad("MacBook Air M1 2020 — comme neuf, vente rapide", "leboncoin", 420, 7, None, 9, type_="revente_directe",
        resale=560, location="Villeurbanne 69100", category="PC portables grand public",
        market={"model": "macbook air m1", "median": 640, "count": 11, "source": "ebay"}),
    _ad("Dyson V10 Absolute — batterie HS", "vinted", 60, 7, 8, 9, panne="batterie à changer", resale=140, piece=35,
        location="Paris 75015", category="Aspirateurs Dyson & robots"),
    _ad("Sonos One (Gen 2) — ne s'allume plus", "leboncoin", 55, 5, 4, 7, panne="alimentation", resale=110, piece=20,
        location="Laon 02000", category="Enceintes & audio"),
    _ad("HP EliteBook 840 G5 — écran à changer", "ebay", 70, 6, 6, 6, panne="dalle fissurée", resale=170, piece=45,
        location="Nantes 44000"),
    _ad("Nintendo Switch OLED — occasion, sous la cote", "vinted", 150, 8, None, 9, type_="revente_directe",
        resale=230, location="Lyon 69007", category="Consoles",
        market={"model": "switch oled", "median": 260, "count": 23, "source": "annonces"}),
    _ad("Lenovo ThinkCentre M720q — sans disque", "ebay", 45, 9, 9, 7, panne="disque absent", resale=120, piece=15,
        location="Bron 69500", category="Mini-PC"),
    _ad("Canapé d'angle convertible", "leboncoin", 120, None, None, None, status="rejected",
        reason="hors catégorie", location="Lyon 69003", category=None),
]

DEMO_PURCHASES = [
    {"title": "Lenovo ThinkPad T470 — sans SSD", "source": "leboncoin", "model": "thinkpad t470",
     "buy_price": 70, "repair_cost": 25, "estimated_resale": 190, "sold_price": 185, "hours_spent": 3,
     "bought_days_ago": 30, "sold_days_ago": 18},
    {"title": "Dyson V8 — batterie HS", "source": "vinted", "model": "dyson v8",
     "buy_price": 45, "repair_cost": 35, "estimated_resale": 150, "sold_price": 160, "hours_spent": 1.5,
     "bought_days_ago": 14, "sold_days_ago": 5},
    {"title": "MacBook Pro 2015 — clavier à changer", "source": "ebay", "model": "macbook pro 2015",
     "buy_price": 180, "repair_cost": 60, "bought_days_ago": 3},  # encore en stock
]


def seed(db: Database) -> None:
    """Remplit une base vide avec les annonces et achats de démonstration."""
    import asyncio

    if db.search(limit=1):
        return
    from .config import DESIRABLE_BRANDS
    from .filters import BRAND_SIGNAL
    from .utils.text import first_keyword, normalize

    for i, (ad, status, reason) in enumerate(DEMO_ADS):
        brand = first_keyword(normalize(ad.title), DESIRABLE_BRANDS)
        if brand and status == "analyzed":  # comme le vrai filtre : badge ⭐ « marque recherchée »
            ad.signals = [*ad.signals, f"{BRAND_SIGNAL} {brand}"]
        asyncio.run(db.remember(ad, status, reason=reason))
        if i in (0, 2):  # un favori, pour montrer la vue « Favoris »
            db.mark(ad.uid, favorite=True)
    today = date.today()
    for demo in DEMO_PURCHASES:  # dates relatives à aujourd'hui : achat toujours avant la revente
        purchase = {k: v for k, v in demo.items() if not k.endswith("_days_ago")}
        purchase["bought_at"] = (today - timedelta(days=demo["bought_days_ago"])).isoformat()
        if "sold_days_ago" in demo:
            purchase["sold_at"] = (today - timedelta(days=demo["sold_days_ago"])).isoformat()
        pid = db.add_purchase(purchase)
        db.record_sale(pid, purchase.get("model"), purchase.get("sold_price"))
