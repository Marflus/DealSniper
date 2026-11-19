"""Passage complet avec sources et API simulées."""

from dealsniper import pipeline
from dealsniper.analyzer import AnalysisResult
from dealsniper.models import Ad
from dealsniper.sources import SourceReport
from dealsniper.storage import DealsStore, RunLog, SeenStore


def fake_sources(*_):
    ads = [
        Ad(title="ThinkPad T480 sans SSD", url="https://www.leboncoin.fr/ad/ordinateurs/2500000001",
           source="leboncoin", price=90, location="Lyon 69003"),
        Ad(title="Canapé cuir", url="https://www.leboncoin.fr/ad/maison/2500000002", source="leboncoin", price=50),
    ]
    return ads, [SourceReport(name="emails", items=1, ads=2)]


def fake_analyze(ads, config, budget):
    for ad in ads:
        ad.analysis = {"type_affaire": "reparation", "score_rentabilite": 8, "confiance": "haute",
                       "marge_nette_estimee": 80}
    return AnalysisResult(analyzed=list(ads), cost_usd=0.002)


def test_full_run_updates_state(monkeypatch, config):
    monkeypatch.setattr(pipeline, "collect_all", fake_sources)
    monkeypatch.setattr(pipeline.analyzer, "analyze", fake_analyze)
    summary = pipeline.run(config, "run")
    assert summary["collected"] == 2 and summary["kept"] == 1 and summary["notified"] == 1
    assert "lbc:2500000001" in SeenStore() and "lbc:2500000002" in SeenStore()
    assert DealsStore().items[0]["notified"] is True
    assert RunLog().cost_today() == 0.002


def test_dry_run_saves_nothing(monkeypatch, config, capsys):
    monkeypatch.setattr(pipeline, "collect_all", fake_sources)
    pipeline.run(config, "dry-run")
    assert len(SeenStore()) == 0 and not RunLog().items
    assert "hors catégorie" in capsys.readouterr().out


def test_failed_analysis_is_retried_later(monkeypatch, config):
    monkeypatch.setattr(pipeline, "collect_all", fake_sources)
    monkeypatch.setattr(pipeline.analyzer, "analyze", lambda ads, c, b: AnalysisResult(failed=list(ads)))
    pipeline.run(config, "run")
    assert "lbc:2500000001" not in SeenStore()  # non analysée → retentée au prochain passage


def test_health_alert_once_per_day(monkeypatch, config):
    sent = []
    monkeypatch.setattr(pipeline.notifier.Notifier, "send_text", lambda self, text: sent.append(text) or True)
    broken = [SourceReport(name="emails", ok=False, error="connexion IMAP impossible")]
    monkeypatch.setattr(pipeline, "collect_all", lambda *a: ([], broken))
    pipeline.run(config, "run")
    pipeline.run(config, "run")
    assert len(sent) == 1 and "IMAP" in sent[0]
