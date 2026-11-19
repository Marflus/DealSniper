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
    return {"id": i, "type_affaire": "reparation", "score_rentabilite": score, "confiance": "haute",
            "panne_probable": "SSD absent", "cout_piece_estime": 20, "valeur_revente_estimee": 200,
            "marge_nette_estimee": 90, "justification_courte": "ok", "question_vendeur": "Chargeur fourni ?"}


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
    assert "JAMAIS une arnaque sans indice concret" in analyzer.SYSTEM_PROMPT
    assert "ne parle jamais d'arnaque pour une enchère" in analyzer.SYSTEM_PROMPT


def deal(score=8, deal_type="reparation"):
    return Ad(title="PS5 Slim", url="https://www.ebay.fr/itm/123456789012", source="ebay", price=250,
              category="Consoles", location="Lyon 69003", seller={"positive_pct": 99.2, "reviews": 1534},
              analysis={**item(1, score), "type_affaire": deal_type})


def test_telegram_format_is_short():
    txt = format_telegram(deal(deal_type="reparation"))
    assert txt.splitlines() == [
        "<b>PS5 Slim</b>",
        "💶 Achat : 250 €",
        "📈 Revente : 200 € (marge +90 €)",
        "🔧 Panne : SSD absent",
        '🔗 <a href="https://www.ebay.fr/itm/123456789012">Voir l\'annonce</a>',
    ]


def test_notify_good_and_watch_digest(monkeypatch, config):
    digests = []
    monkeypatch.setattr("dealsniper.notifier.Notifier.send_digest",
                        lambda self, header, ads: digests.append([a.score for a in ads]) or True)
    sent = notify([deal(8), deal(6), deal(5), deal(3)], config)
    assert [a.score for a in sent] == [8] and digests == [[6, 5]]
