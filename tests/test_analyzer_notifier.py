import json
from types import SimpleNamespace

from dealsniper import analyzer
from dealsniper.models import Ad
from dealsniper.notifier import format_telegram, notify


class FakeMessages:
    def __init__(self, payloads):
        self.payloads, self.calls = list(payloads), []

    def create(self, **params):
        self.calls.append(params)
        stop, data = self.payloads.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(data))], stop_reason=stop,
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


def make_analyzer(monkeypatch, config, payloads):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    an = analyzer.Analyzer(config)
    an.client = SimpleNamespace(messages=FakeMessages(payloads))
    return an


def ads(n):
    return [Ad(title=f"ThinkPad {i}", url=f"https://e.x/{i}", source="t", price=50, category="PC portables pros",
               description="x" * 2000) for i in range(n)]


def item(i, score=8):
    return {"id": i, "type_affaire": "reparation", "score_rentabilite": score, "facilite_reparation": 9,
            "facilite_vente": 8, "panne_probable": "SSD absent", "cout_piece_estime": 20, "valeur_revente_estimee": 200,
            "marge_nette_estimee": 90}


def test_batch_schema_truncation_and_cost(monkeypatch, config):
    an = make_analyzer(monkeypatch, config, [("end_turn", {"annonces": [item(1), item(2, 15)]})])
    res = an.analyze(ads(2))
    assert len(res.analyzed) == 2 and res.analyzed[1].analysis["score_rentabilite"] == 10 and res.cost_usd > 0
    call = an.client.messages.calls[0]
    assert len(call["messages"][0]["content"]) < 2 * 750
    assert call["output_config"]["format"]["type"] == "json_schema"


def test_split_on_max_tokens_and_budget(monkeypatch, config):
    an = make_analyzer(monkeypatch, config, [("max_tokens", {"annonces": []}),
                                             ("end_turn", {"annonces": [item(1)]}),
                                             ("end_turn", {"annonces": [item(1)]})])
    assert len(an.analyze(ads(2)).analyzed) == 2
    an = make_analyzer(monkeypatch, config, [("end_turn", {"annonces": [item(i) for i in range(1, 11)]})])
    res = an.analyze(ads(20), budget_left_usd=0.0001)
    assert len(res.analyzed) == 10 and len(res.failed) == 10


def test_prompt_rules():
    assert "pas un indice d'arnaque" in analyzer.SYSTEM_PROMPT
    assert "enchère actuelle" in analyzer.SYSTEM_PROMPT and "facilite_reparation" in analyzer.SYSTEM_PROMPT


def deal(score=8, deal_type="reparation"):
    return Ad(title="PS5 Slim", url="https://www.ebay.fr/itm/123456789012", source="ebay", price=250,
              category="Consoles", location="Lyon 69003", seller={"positive_pct": 99.2, "reviews": 1534},
              analysis={**item(1, score), "type_affaire": deal_type})


def test_telegram_format_is_short():
    txt = format_telegram(deal(deal_type="reparation"))
    assert txt.splitlines() == [
        "<b>PS5 Slim</b>",
        "💶 Achat : 250 €",
        "📈 Revente : 200 € (marge +90 €) · vente 8/10",
        "🔧 Panne : SSD absent (facilité 9/10)",
        '🔗 <a href="https://www.ebay.fr/itm/123456789012">Voir l\'annonce</a>',
    ]


def test_notify_good_and_watch_digest(monkeypatch, config):
    digests = []
    monkeypatch.setattr("dealsniper.notifier.Notifier.send_digest",
                        lambda self, header, ads: digests.append([a.score for a in ads]) or True)
    sent = notify([deal(8), deal(6), deal(5), deal(3)], config)
    assert [a.score for a in sent] == [8] and digests == [[6, 5]]


def test_sanitize_ease_score_and_schema():
    clean = analyzer.sanitize_analysis({"facilite_reparation": "12", "score_rentabilite": "x"})
    assert clean["facilite_reparation"] == 10 and clean["score_rentabilite"] == 1
    required = analyzer.RESPONSE_SCHEMA["properties"]["annonces"]["items"]["required"]
    assert "facilite_vente" in required and analyzer.sanitize_analysis({})["facilite_vente"] == 5
    assert "facilite_reparation" in required and "justification_courte" not in required


def test_notify_min_margin_and_ease(monkeypatch, config):
    monkeypatch.setattr("dealsniper.notifier.Notifier.send_ad", lambda self, ad: True)
    monkeypatch.setattr("dealsniper.notifier.time.sleep", lambda s: None)
    cfg = {**config, "notifier": {**config["notifier"], "watch_score": None, "min_margin": 50, "min_ease": 7}}
    small, hard, direct = deal(8), deal(8), deal(8, "revente_directe")
    small.analysis["marge_nette_estimee"] = 20
    hard.analysis["facilite_reparation"] = 3
    direct.analysis["facilite_reparation"] = 1  # la facilité ne compte pas pour la revente directe
    sent = notify([small, hard, direct, deal(9)], cfg)
    assert [(a.score, a.deal_type) for a in sent] == [(9, "reparation"), (8, "revente_directe")]


def test_make_realistic_recomputes_margin_and_caps_score():
    ad = Ad(title="MacBook Pro 2017 à réparer", url="https://e.x/1", source="t", price=70)
    optimistic = {**analyzer.sanitize_analysis(item(1, score=8)), "valeur_revente_estimee": 600,
                  "cout_piece_estime": 60, "marge_nette_estimee": 378}
    clean = analyzer.make_realistic(optimistic, ad, haircut=0.9)
    assert clean["valeur_revente_estimee"] == 540
    assert clean["marge_nette_estimee"] == 540 - 70 - 60 - 54  # recalculée, pas celle du modèle
    thin = analyzer.make_realistic({**optimistic, "valeur_revente_estimee": 150}, ad, haircut=0.9)
    assert thin["marge_nette_estimee"] < 20 and thin["score_rentabilite"] == 4


def test_haiku_5_5_runs_without_thinking_at_low_effort(monkeypatch, config):
    cfg = {**config, "analyzer": {**config["analyzer"], "model": "claude-haiku-5-5", "effort": None}}
    an = make_analyzer(monkeypatch, cfg, [("end_turn", {"annonces": [item(1)]})])
    an.analyze(ads(1))
    call = an.client.messages.calls[0]
    assert call["thinking"] == {"type": "disabled"} and call["output_config"]["effort"] == "low"
