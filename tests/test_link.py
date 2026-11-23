"""Onglet Analyse : lecture d'un lien collé et bilan par Claude Opus 5.5."""

import json
from types import SimpleNamespace as NS

import pytest

from dealsniper.link_reader import LinkUnreadable, check_url, fetch_ad, parse_page

VINTED_PAGE = """<html><head><meta property="og:title" content="Titre OG">
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Product",
 "name": "ThinkCentre M920q i5 16 Go", "description": "Fonctionne tr&egrave;s bien, <b>sans</b> alim",
 "image": ["https://img.vinted.net/1.jpg", "https://img.vinted.net/2.jpg"],
 "offers": {"@type": "Offer", "price": "95.00", "priceCurrency": "EUR", "availability": "https://schema.org/InStock"}}
</script></head><body></body></html>"""

LBC_PAGE = """<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {"ad": {
 "subject": "Mini PC Dell Optiplex 7070", "body": "Bon état<br>Windows 11", "price": [120],
 "images": {"urls_large": ["https://img.leboncoin.fr/a.jpg"]},
 "location": {"city": "Lyon", "zipcode": "69003"}}}}}</script>"""

OG_PAGE = """<meta property="og:title" content="Steam Deck OLED 512 Go &amp; housse">
<meta property="og:description" content="Comme neuf"><meta property="og:image" content="/photo.jpg">
<meta property="product:price:amount" content="390,50">"""


def test_parse_vinted_json_ld():
    found = parse_page(VINTED_PAGE, "https://www.vinted.fr/items/1")
    assert found["title"] == "ThinkCentre M920q i5 16 Go" and found["price"] == 95
    assert "sans alim" in found["description"]
    assert found["photos"][:2] == ["https://img.vinted.net/1.jpg", "https://img.vinted.net/2.jpg"]
    assert "available" not in found


def test_parse_leboncoin_page_data():
    found = parse_page(LBC_PAGE, "https://www.leboncoin.fr/ad/informatique/1")
    assert found["title"] == "Mini PC Dell Optiplex 7070" and found["price"] == 120
    assert found["location"] == "Lyon 69003" and found["photos"] == ["https://img.leboncoin.fr/a.jpg"]


def test_parse_open_graph_fallback():
    found = parse_page(OG_PAGE, "https://boutique.example.fr/p/1")
    assert found["title"] == "Steam Deck OLED 512 Go & housse" and found["price"] == 390.5
    assert found["photos"] == ["https://boutique.example.fr/photo.jpg"]
    assert parse_page("<html>rien</html>", "https://x.fr") == {}


@pytest.mark.parametrize("url", ["http://localhost:5000/x", "http://127.0.0.1/x", "http://192.168.1.10/a",
                                 "file:///etc/passwd", "vinted"])
def test_check_url_refuses_local_or_invalid(url):
    with pytest.raises(ValueError):
        check_url(url)


def test_fetch_ad_blocked_site_asks_for_manual_entry(config):
    def blocked(url, headers):
        return NS(status_code=403, text="captcha", url=url)

    with pytest.raises(LinkUnreadable, match="à la main"):
        fetch_ad("https://www.leboncoin.fr/ad/informatique/1", config, http=blocked)
    ad, photos = fetch_ad("https://www.vinted.fr/items/1-mini-pc?referrer=catalog", config,
                          http=lambda url, headers: NS(status_code=200, text=VINTED_PAGE, url=url))
    assert ad.source == "vinted" and ad.price == 95 and len(photos) == 2


BILAN = {"revente_basse": 140, "revente_typique": 160, "revente_haute": 180, "confiance": "élevée",
         "sources": ["eBay vendus : 150-170 €"], "resume": "Bon prix.", "pieges": "alim absente",
         "ventes": [{"date": "2026-10", "prix": 155, "source": "ebay"}], "message_negociation": "Bonjour…",
         "photo": {"etat": "bon", "constat": "Boîtier propre.", "alertes": []},
         "modele_exact": "M920q i5-8500T", "note": 8.4, "points_forts": ["prix", "16 Go"],
         "points_faibles": ["sans alim"], "arnaque": {"niveau": "eleve", "raisons": ["paiement hors site"]},
         "a_verifier": ["démarrage", "BIOS sans mot de passe"], "questions": ["Le BIOS est-il verrouillé ?"]}


def fake_client(calls):
    def create(**kwargs):
        calls.append(kwargs)
        usage = NS(input_tokens=10_000, output_tokens=2_000, server_tool_use=NS(web_search_requests=4))
        return NS(content=[NS(type="text", text="Voici\n" + json.dumps(BILAN))], stop_reason="end_turn",
                  usage=usage, model="claude-opus-5-5")
    return NS(beta=NS(messages=NS(create=create)))


def test_analyze_link_uses_opus_with_photos_and_full_bilan(config, monkeypatch):
    from dealsniper.deep import analyze_link
    from dealsniper.models import Ad

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    calls = []
    jpeg = b"\xff\xd8\xff" + b"0" * 100
    ad = Ad(title="ThinkCentre M920q", url="https://www.vinted.fr/items/1", source="vinted", price=95)
    result = analyze_link(ad, ["https://a/1.jpg", "https://a/2.jpg"], config, client=fake_client(calls),
                          fetch=lambda url: jpeg)
    call = calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["output_config"]["effort"] == "high"
    assert [b["type"] for b in call["messages"][0]["content"]] == ["image", "image", "text"]
    assert result["note"] == 8.4 and result["arnaque"]["niveau"] == "élevé"
    assert result["prix_achat_max"] == 160 * 0.9 - 48 and result["photos_analysees"] == 2
    assert result["questions"] == ["Le BIOS est-il verrouillé ?"]
    assert result["cost_usd"] == round((10_000 * 4 + 2_000 * 20) / 1e6 + 4 * 0.01, 4)


def test_link_analysis_endpoint(client, monkeypatch):
    c, _ = client
    seen = []

    def fake(ad, photos, config, **kw):
        seen.append((ad, photos))
        return {**BILAN, "arnaque": {"niveau": "faible", "raisons": []}, "prix_achat_max": 96, "cost_usd": 0.2}

    monkeypatch.setattr("dealsniper.deep.analyze_link", fake)
    monkeypatch.setattr("dealsniper.link_reader.fetch_ad", lambda url, config: (_ for _ in ()).throw(
        LinkUnreadable("leboncoin refuse la lecture automatique : collez … à la main.")))
    resp = c.post("/api/link-analysis", json={"url": "https://www.leboncoin.fr/ad/x/1"})
    assert resp.status_code == 422 and resp.get_json()["manual"] is True
    assert c.post("/api/link-analysis", json={"url": "http://127.0.0.1/x"}).status_code == 400
    # Saisie manuelle
    data = c.post("/api/link-analysis", json={"url": "https://www.leboncoin.fr/ad/x/1", "title": "Mini PC Dell",
                                              "price": "120", "description": "Bon état"}).get_json()
    assert data["ok"] and data["item"]["ad"]["source"] == "leboncoin" and seen[-1][0].price == 120
    # Annonce déjà en base : aucune requête vers le site
    data = c.post("/api/link-analysis", json={"url": "https://www.ebay.fr/itm/100000000001"}).get_json()
    assert data["ok"] and seen[-1][0].title == "ThinkPad T480 sans SSD"
    items = c.get("/api/link-analyses").get_json()["items"]
    assert [i["ad"]["title"] for i in items] == ["ThinkPad T480 sans SSD", "Mini PC Dell"]
    assert c.delete(f"/api/link-analyses/{items[0]['id']}").get_json()["ok"]
    assert len(c.get("/api/link-analyses").get_json()["items"]) == 1
