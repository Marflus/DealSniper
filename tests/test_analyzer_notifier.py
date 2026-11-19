import json
from types import SimpleNamespace

from dealsniper import analyzer
from dealsniper.models import Ad
from dealsniper.notifier import format_discord, format_telegram, notify


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


def make_analyzer(monkeypatch, config, payloads):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    an = analyzer.Analyzer(config)
    an.client = SimpleNamespace(messages=FakeMessages(payloads))
    return an


def ads(n):
    return [Ad(title=f"ThinkPad {i}", url=f"https://e.x/{i}", source="t", price=50, category="PC portables pros",
               description="x" * 2000) for i in range(n)]


def item(i, score=8, **extra):
    return {"id": i, "type_affaire": "reparation", "score_rentabilite": score, "confiance": "haute",
            "panne_probable": "SSD absent", "cout_piece_estime": 20, "valeur_revente_estimee": 200,
            "marge_nette_estimee": 90, "justification_courte": "ok", "question_vendeur": "Chargeur fourni ?", **extra}


def test_batch_prompt_schema_and_cost(monkeypatch, config):
    an = make_analyzer(monkeypatch, config, [("end_turn", {"annonces": [item(1), item(2, 15)]})])
    res = an.analyze(ads(2))
    assert len(res.analyzed) == 2 and not res.failed and res.cost_usd > 0
    assert res.analyzed[1].analysis["score_rentabilite"] == 10  # borné
    call = an.client.messages.calls[0]
    assert len(call["messages"][0]["content"]) < 2 * 750  # descriptions tronquées
    schema = call["output_config"]["format"]["schema"]
    assert {"type_affaire", "confiance", "question_vendeur"} <= set(schema["properties"]["annonces"]["items"]["required"])


def test_missing_items_split_and_budget(monkeypatch, config):
    an = make_analyzer(monkeypatch, config, [("end_turn", {"annonces": [item(1)]})])
    res = an.analyze(ads(2))
    assert len(res.analyzed) == 1 and len(res.failed) == 1

    an = make_analyzer(monkeypatch, config, [
        ("max_tokens", {"annonces": []}),
        ("end_turn", {"annonces": [item(1)]}),
        ("end_turn", {"annonces": [item(1)]}),
    ])
    assert len(an.analyze(ads(2)).analyzed) == 2

    an = make_analyzer(monkeypatch, config, [("end_turn", {"annonces": [item(i) for i in range(1, 11)]})])
    res = an.analyze(ads(20), budget_left_usd=0.0001)
    assert len(res.analyzed) == 10 and len(res.failed) == 10


def test_sanitize_analysis():
    clean = analyzer.sanitize_analysis({"type_affaire": "n'importe quoi", "confiance": "???", "score_rentabilite": "7.6"})
    assert clean["type_affaire"] == "reparation" and clean["confiance"] == "moyenne" and clean["score_rentabilite"] == 8


def make_deal(deal_type, score=8):
    return Ad(title="PS5 Slim", url="https://e.x/1", source="leboncoin", price=250, category="Consoles",
              location="Lyon 69003", analysis={**item(1, score), "type_affaire": deal_type})


def test_formatting():
    txt = format_telegram(make_deal("revente_directe"))
    assert "Revente directe" in txt and "Panne probable" not in txt and "Lyon 69003" in txt and "Chargeur" in txt
    assert "Panne probable" in format_telegram(make_deal("reparation"))
    assert "Revente directe" in format_discord(make_deal("revente_directe"))["title"]


def test_notify_threshold_and_types(config):
    cfg = {**config, "notifier": {**config["notifier"], "deal_types": ["reparation"]}}
    sent = notify([make_deal("revente_directe"), make_deal("reparation"), make_deal("reparation", 5)], cfg)
    assert [a.deal_type for a in sent] == ["reparation"]


def test_watch_digest_for_mid_scores(monkeypatch, config):
    digests = []
    monkeypatch.setattr("dealsniper.notifier.Notifier.send_digest",
                        lambda self, header, ads: digests.append((header, [a.score for a in ads])) or True)
    sent = notify([make_deal("reparation", 5), make_deal("reparation", 6), make_deal("reparation", 3)], config)
    assert sent == [] and digests == [("👀 À vérifier (2 annonce(s) notée(s) 5-6/10)", [6, 5])]


def test_digest_chunks_and_delivery_flag(capsys):
    from dealsniper.notifier import Notifier, _chunks

    deal = make_deal("reparation", 6)
    deal.description = "Livraison possible"
    assert deal.delivery_possible
    Notifier().send_digest("Récap", [deal])
    assert "6/10" in capsys.readouterr().out
    assert len(_chunks("H", ["x" * 1000] * 10)) > 1


def test_ad_roundtrip():
    deal = make_deal("revente_directe")
    assert Ad.from_dict({**deal.to_dict(), "notified": True}).uid == deal.uid


def test_prompt_forbids_scam_claims_without_evidence():
    assert "JAMAIS une arnaque sans indice concret" in analyzer.SYSTEM_PROMPT
    assert "pas le score" in analyzer.SYSTEM_PROMPT
