import json
from types import SimpleNamespace

import analyzer
from core import Ad, load_config


class FakeMessages:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def create(self, **params):
        self.calls.append(params)
        stop, data = self.payloads.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(data))],
            stop_reason=stop,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )


def make_analyzer(monkeypatch, payloads):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    an = analyzer.Analyzer(load_config())
    an.client = SimpleNamespace(messages=FakeMessages(payloads))
    return an


def ads(n):
    return [Ad(title=f"ThinkPad {i}", url=f"https://e.x/{i}", source="t", price=50, category="PC portables pros",
               description="x" * 2000) for i in range(n)]


def item(i, score=8):
    return {"id": i, "score_rentabilite": score, "panne_probable": "SSD absent", "cout_piece_estime": 20,
            "valeur_revente_estimee": 200, "marge_nette_estimee": 90, "justification_courte": "ok"}


def test_batch_analysis_and_truncation(monkeypatch):
    an = make_analyzer(monkeypatch, [("end_turn", {"annonces": [item(1), item(2, 15)]})])
    res = an.analyze(ads(2))
    assert len(res.analyzed) == 2 and not res.failed
    assert res.analyzed[1].analysis["score_rentabilite"] == 10  # borné
    prompt = an.client.messages.calls[0]["messages"][0]["content"]
    assert len(prompt) < 2 * 750  # descriptions tronquées à ~600 caractères
    assert an.client.messages.calls[0]["output_config"]["format"]["type"] == "json_schema"
    assert res.cost_usd > 0


def test_missing_items_are_reported_as_failed(monkeypatch):
    an = make_analyzer(monkeypatch, [("end_turn", {"annonces": [item(1)]})])
    res = an.analyze(ads(2))
    assert len(res.analyzed) == 1 and len(res.failed) == 1


def test_max_tokens_splits_batch(monkeypatch):
    an = make_analyzer(monkeypatch, [
        ("max_tokens", {"annonces": []}),
        ("end_turn", {"annonces": [item(1)]}),
        ("end_turn", {"annonces": [item(1)]}),
    ])
    res = an.analyze(ads(2))
    assert len(res.analyzed) == 2


def test_budget_stops_analysis(monkeypatch):
    an = make_analyzer(monkeypatch, [("end_turn", {"annonces": [item(i) for i in range(1, 11)]})])
    res = an.analyze(ads(20), budget_left_usd=0.0001)
    assert len(res.analyzed) == 10 and len(res.failed) == 10
