"""Analyse approfondie : coût réel (tokens + recherches web) et prix d'achat maximum."""

from types import SimpleNamespace as NS

from dealsniper.deep import analyze_market, max_buy, refresh
from dealsniper.models import Ad


def _resp(text, stop, searches):
    usage = NS(input_tokens=1_000, output_tokens=500, server_tool_use=NS(web_search_requests=searches))
    return NS(content=[NS(type="text", text=text)], stop_reason=stop, usage=usage)


def test_cost_counts_web_searches(config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    final = ('{"revente_basse": 180, "revente_typique": 200, "revente_haute": 230, "confiance": "élevée",'
             ' "sources": [], "resume": "ok", "pieges": "", "ventes": []}')
    replies = iter([_resp("", "pause_turn", 2), _resp(final, "end_turn", 1)])
    client = NS(messages=NS(create=lambda **kw: next(replies)))
    result = analyze_market(Ad(title="ThinkPad T480", url="https://x/1", source="ebay", price=90), config, client)
    tokens = 2 * (1_000 * 2.00 + 500 * 10.00) / 1e6  # Sonnet 5.5, deux réponses
    assert result["cost_usd"] == round(tokens + 3 * 0.01, 4)  # + 3 recherches web à 0,01 $
    assert result["prix_achat_max"] == 120  # 200 × 0,9 − 60 (30 % de 200)


def test_max_buy_uses_typical_resale():
    # marge visée proportionnelle : 30 % de la revente, au moins 10 €
    assert max_buy(200) == 120 and max_buy(200, piece=25) == 95 and max_buy(None) is None
    assert max_buy(25) == 12  # petit objet : achat max positif (−16 € avec l'ancien +30 € fixe)
    old = {"revente_basse": 60, "revente_typique": 80, "prix_achat_max": -21}
    assert refresh(old, piece=10)["prix_achat_max"] == 38  # 80 × 0,9 − 10 − 24 (30 % de 80)


def test_empty_or_refused_analysis_is_not_saved_but_charged(client, monkeypatch):
    import pytest

    from dealsniper.deep import AnalysisFailed

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    refused = NS(content=[], stop_reason="refusal",
                 usage=NS(input_tokens=1_000, output_tokens=0, server_tool_use=NS(web_search_requests=1)))
    client_api = NS(messages=NS(create=lambda **kw: refused))
    from dealsniper.config import load_config
    from dealsniper.deep import analyze_market

    config = load_config()
    with pytest.raises(AnalysisFailed) as err:
        analyze_market(Ad(title="X", url="https://x/1", source="ebay", price=10), config, client_api)
    assert err.value.cost_usd > 0.01  # tokens + 1 recherche web : à compter quand même

    c, _ = client

    def boom(ad, cfg):
        raise AnalysisFailed("Le modèle a refusé cette analyse.", 0.05)

    monkeypatch.setattr("dealsniper.deep.analyze_market", boom)
    before = c.get("/api/progress").get_json()["cost_today"]
    resp = c.post("/api/ads/ebay:100000000001/market-analysis")
    assert resp.status_code == 502 and "refusé" in resp.get_json()["error"]
    assert c.get("/api/progress").get_json()["cost_today"] == round(before + 0.05, 4)
    ad = next(a for a in c.get("/api/ads").get_json()["ads"] if a["uid"] == "ebay:100000000001")
    assert "market_deep" not in ad  # rien d'enregistré


def test_photo_is_sent_and_its_report_kept(config, monkeypatch):
    """La photo de l'annonce part avec la demande (bloc image) ; le constat de l'IA est gardé et nettoyé."""
    import base64

    from dealsniper.deep import photo_block

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 100
    assert photo_block("https://img/1.jpg", fetch=lambda url: jpeg)["source"]["media_type"] == "image/jpeg"
    assert photo_block("https://img/1.jpg", fetch=lambda url: b"<html>pas une image</html>") is None
    assert photo_block("https://img/1.jpg", fetch=lambda url: b"\xff\xd8\xff" + b"0" * 5_000_000) is None
    assert photo_block(None) is None

    sent = []
    answer = ('{"revente_basse": 150, "revente_typique": 180, "revente_haute": 200, "confiance": "moyenne",'
              ' "photo": {"etat": "MAUVAIS", "constat": "Écran fissuré en haut à gauche.",'
              ' "alertes": ["fissure visible", "", "coin enfoncé"]}}')

    def create(**params):
        sent.append(params["messages"][0]["content"])
        return _resp(answer, "end_turn", 0)

    ad = Ad(title="iPhone 12", url="https://x/1", source="vinted", price=120, image="https://img/1.jpg")
    result = analyze_market(ad, config, NS(messages=NS(create=create)), fetch=lambda url: jpeg)
    assert sent[0][0]["type"] == "image" and base64.b64decode(sent[0][0]["source"]["data"]) == jpeg
    assert result["photo"] == {"etat": "mauvais", "constat": "Écran fissuré en haut à gauche.",
                               "alertes": ["fissure visible", "coin enfoncé"]}

    sent.clear()  # sans photo : texte seul, et pas de constat même si le modèle en invente un
    result = analyze_market(Ad(title="iPhone 12", url="https://x/2", source="vinted", price=120), config,
                            NS(messages=NS(create=create)))
    assert isinstance(sent[0], str) and result["photo"] is None


def test_card_follows_the_deep_verdict():
    """Switch OLED à 20 € notée 9/10 (+110 €) : l'analyse approfondie voit des accessoires (≈ 15 €) → la carte suit."""
    from dealsniper.deep import apply_to_card

    quick = {"score_rentabilite": 9, "valeur_revente_estimee": 144, "marge_nette_estimee": 110, "cout_piece_estime": 0}
    card = apply_to_card(quick, {"revente_basse": 10, "revente_typique": 15}, 20)
    assert card["score_rentabilite"] <= 3 and card["valeur_revente_estimee"] == 15
    assert card["marge_nette_estimee"] == -6.5 and card["score_rapide"] == 9
    assert card["source_revente"] == "analyse approfondie"
    assert apply_to_card(card, {"revente_typique": 15}, 20) == card  # appliqué deux fois : même résultat

    bargain = apply_to_card({**quick, "score_rentabilite": 4}, {"revente_typique": 300}, 100)
    assert bargain["score_rentabilite"] >= 7  # 300 × 0,9 − 90 = 180 € max, demandé 100 € : à acheter
    doubtful = apply_to_card({**quick, "score_rentabilite": 2}, {"revente_typique": 650, "confiance": "faible"}, 124)
    assert doubtful["score_rentabilite"] == 2  # MacBook à 124 € peut-être une arnaque : pas de hausse
    nego = apply_to_card({**quick, "score_rentabilite": 9}, {"revente_typique": 300}, 200)
    assert 5 <= nego["score_rentabilite"] <= 6  # 200 € pour 180 € max : à négocier
    assert apply_to_card(quick, None, 20) is quick
