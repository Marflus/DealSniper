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
    assert result["prix_achat_max"] == 150  # 200 × 0,9 − 30


def test_max_buy_uses_typical_resale():
    assert max_buy(200) == 150 and max_buy(200, piece=25) == 125 and max_buy(None) is None
    old = {"revente_basse": 60, "revente_typique": 80, "prix_achat_max": -21}
    assert refresh(old, piece=10)["prix_achat_max"] == 32  # 80 × 0,9 − 10 − 30


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
