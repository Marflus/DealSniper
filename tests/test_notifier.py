from core import Ad
from notifier import format_discord, format_telegram, notify


def make(deal_type, score=8):
    return Ad(title="PS5 Slim", url="https://e.x/1", source="t", price=250, category="Consoles",
              analysis={"type_affaire": deal_type, "score_rentabilite": score, "panne_probable": "aucune",
                        "cout_piece_estime": 0, "valeur_revente_estimee": 380, "marge_nette_estimee": 92,
                        "justification_courte": "Vente urgente, 35 % sous la cote"})


def test_direct_resale_formatting():
    txt = format_telegram(make("revente_directe"))
    assert "Revente directe" in txt and "Panne probable" not in txt
    assert "Panne probable" in format_telegram(make("reparation"))
    assert "Revente directe" in format_discord(make("revente_directe"))["title"]


def test_notify_respects_deal_types(monkeypatch, capsys):
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("notifier.time.sleep", lambda s: None)
    cfg = {"notifier": {"min_score": 7, "deal_types": ["reparation"]}}
    sent = notify([make("revente_directe"), make("reparation"), make("reparation", 5)], cfg)
    assert [a.analysis["type_affaire"] for a in sent] == ["reparation"]
