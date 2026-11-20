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
    assert len(res.analyzed) == 10 and len(res.failed) == 10  # absentes de la réponse : échecs comptés


def test_budget_and_cancel_defer_without_counting_failures(monkeypatch, config):
    config["analyzer"]["batch_size"] = 10
    an = make_analyzer(monkeypatch, config, [("end_turn", {"annonces": [item(i) for i in range(1, 11)]})])
    res = an.analyze(ads(20), budget_left_usd=0.0001)  # plafond atteint après le 1er lot
    assert len(res.analyzed) == 10 and len(res.deferred) == 10 and not res.failed
    an = make_analyzer(monkeypatch, config, [])
    res = an.analyze(ads(5), should_stop=lambda: True)  # annulé avant le premier appel
    assert len(res.deferred) == 5 and not an.client.messages.calls


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
    monkeypatch.setattr("dealsniper.notifier._pause", lambda s: None)
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


def test_market_quote_caps_the_ai_resale():
    ad = Ad(title="ThinkPad T480 sans SSD", url="https://e.x/2", source="t", price=90,
            market={"model": "thinkpad t480", "median": 200, "count": 12, "source": "annonces"})
    optimistic = {**analyzer.sanitize_analysis(item(1)), "valeur_revente_estimee": 350, "cout_piece_estime": 20}
    clean = analyzer.make_realistic(optimistic, ad, haircut=0.9)
    assert clean["valeur_revente_estimee"] == 180 and clean["source_revente"] == "cote"  # 200 € × 0,9
    assert clean["valeur_revente_ia"] == 350
    assert "cote 200 €" in analyzer.format_ad(1, ad, 400)


def test_only_pepites_get_an_instant_message(monkeypatch, config):
    sent_ad, digests = [], []
    monkeypatch.setattr("dealsniper.notifier.Notifier.send_ad", lambda self, ad: sent_ad.append(ad.score) or True)
    monkeypatch.setattr("dealsniper.notifier.Notifier.send_digest",
                        lambda self, header, ads: digests.append((header, [a.score for a in ads])) or True)
    cfg = {**config, "notifier": {**config["notifier"], "min_score": 7, "instant_min_score": 9, "watch_score": None}}
    from dealsniper.notifier import notify
    sent = notify([deal(9), deal(8), deal(7), deal(4)], cfg)
    assert sent_ad == [9]  # seule la pépite (≥9) en message immédiat
    assert any(h.startswith("💡 Bonnes affaires") and s == [8, 7] for h, s in digests)  # 7-8 regroupées
    assert {a.score for a in sent} == {9, 8, 7}


def test_reprice_after_price_drop():
    ad = Ad(title="ThinkPad", url="https://e.x/1", source="t", price=170)
    raw = analyzer.sanitize_analysis(item(1, score=8) | {"valeur_revente_estimee": 2000 / 9, "cout_piece_estime": 20})
    first = analyzer.make_realistic(raw, ad)  # revente 200 : marge 200 − 170 − 20 − 20 = −10 → note plafonnée
    assert first["marge_nette_estimee"] == -10 and first["score_rentabilite"] == 4 and first["score_ia"] == 8
    again = analyzer.reprice(first, 110)  # baisse à 110 € : marge 50 → la note de l'IA revient
    assert again["marge_nette_estimee"] == 50 and again["score_rentabilite"] == 8
    assert analyzer.reprice(first, 110, "enchere") is first  # enchère : prix provisoire, rien à recalculer
    free = {"realiste": True, "sans_ia": True, "valeur_revente_estimee": 200, "marque_recherchee": True}
    assert analyzer.reprice(free, 100)["score_rentabilite"] == 9  # marge 80 → 8, +1 marque recherchée


def test_post_retries_rate_limits_and_server_errors(monkeypatch):
    import requests

    from dealsniper import notifier

    waits, calls = [], []

    class R:
        def __init__(self, code, body=None):
            self.status_code, self.ok, self.text, self._body = code, code < 400, "", body or {}

        def json(self):
            return self._body

    def run(responses):
        calls.clear()
        waits.clear()
        seq = iter(responses)

        def post(url, json=None, timeout=None):
            calls.append(url)
            item = next(seq)
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(notifier.requests, "post", post)
        return notifier._post("https://api.example/send", {}, "Test")

    monkeypatch.setattr(notifier, "_pause", waits.append)
    rate_limited = R(429, {"parameters": {"retry_after": 3}})
    assert run([rate_limited, R(200)]) is True and waits == [3.0]  # attend le délai demandé
    assert run([R(502), R(200)]) is True and len(calls) == 2  # erreur serveur : réessayé
    assert run([requests.ConnectionError("coupé"), R(200)]) is True  # réseau : réessayé
    assert run([R(400)]) is False and len(calls) == 1  # requête refusée : pas d'insistance
    assert run([R(500), R(500), R(500)]) is False and len(calls) == notifier.MAX_ATTEMPTS


def test_api_errors_are_deferred_failed_or_retried(monkeypatch, config):
    """Chaque erreur de l'API mène au bon sort : reportée (pas la faute de l'annonce), échec compté,
    ou nouvel essai avec une requête adaptée au modèle."""
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def error(cls, status, message):
        return cls(message, response=httpx.Response(status, request=request), body=None)

    def analyzer_with(effects):
        an = make_analyzer(monkeypatch, config, [])
        calls = []

        def create(**params):
            calls.append(params)
            effect = effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
            stop, data = effect
            text = data if isinstance(data, str) else json.dumps(data)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop,
                                   usage=SimpleNamespace(input_tokens=100, output_tokens=20))

        an.client = SimpleNamespace(messages=SimpleNamespace(create=create))
        return an, calls

    # Clé refusée : toute l'analyse s'arrête, tout est reporté (pas un échec de l'annonce).
    an, _ = analyzer_with([error(anthropic.AuthenticationError, 401, "invalid x-api-key")])
    res = an.analyze(ads(3))
    assert len(res.deferred) == 3 and not res.failed

    # Crédit épuisé : idem.
    an, _ = analyzer_with([error(anthropic.BadRequestError, 400, "Your credit balance is too low")])
    assert len(an.analyze(ads(2)).deferred) == 2

    # Réseau : reporté.
    an, _ = analyzer_with([anthropic.APIConnectionError(request=request)])
    assert len(an.analyze(ads(2)).deferred) == 2

    # Schéma JSON refusé par le modèle : nouvel essai en JSON simple, et ça marche.
    an, calls = analyzer_with([error(anthropic.BadRequestError, 400, "output_config.format not supported"),
                               ("end_turn", {"annonces": [item(1), item(2)]})])
    res = an.analyze(ads(2))
    assert len(res.analyzed) == 2 and "format" not in calls[1].get("output_config", {})

    # Réponse illisible : échec compté (l'annonce sera abandonnée après quelques essais).
    an, _ = analyzer_with([("end_turn", "pas du json")])
    res = an.analyze(ads(2))
    assert len(res.failed) == 2 and not res.deferred

    # Refus du modèle : échec compté.
    an, _ = analyzer_with([("refusal", {})])
    assert len(an.analyze(ads(1)).failed) == 1
