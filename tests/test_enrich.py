import json

from dealsniper import enrich
from dealsniper.models import Ad

NEXT_DATA = {"props": {"pageProps": {"ad": {
    "body": "ThinkPad T480, écran OK, vendu sans SSD ni chargeur. Batterie tient 2 h.",
    "attributes": [{"key": "condition", "value_label": "Bon état"}],
    "owner": {"type": "private", "name": "Jean", "rating_score": 0.96, "rating_count": 42,
              "registered_at": "2019-03-12T10:00:00"},
}}}}
PAGE = f"""<html><head><meta property="og:description" content="résumé court"></head><body>
<script id="__NEXT_DATA__" type="application/json">{json.dumps(NEXT_DATA)}</script></body></html>"""


def test_parse_page_reads_description_and_seller():
    info = enrich.parse_page(PAGE)
    assert info.description.startswith("ThinkPad T480")
    assert info.seller_type == "private"
    assert info.seller == {"rating": 0.96, "reviews": 42, "since": "2019-03-12T10:00:00"}


def test_parse_page_falls_back_to_meta_description():
    info = enrich.parse_page('<meta property="og:description" content="Console PS4 qui surchauffe">')
    assert info.description == "Console PS4 qui surchauffe" and info.seller == {}


def test_apply_merges_into_ad():
    ad = Ad(title="ThinkPad T480", url="https://www.leboncoin.fr/vi/1234567.htm", source="leboncoin", price=90)
    assert enrich._apply(ad, enrich.parse_page(PAGE))
    assert "sans SSD" in ad.description and "Vendeur : private" in ad.description
    assert ad.seller == {"positive_pct": 96.0, "reviews": 42, "since": "2019-03-12"}


class FakeResponse:
    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def get(self, url, **kw):
        self.calls += 1
        return self.responses.pop(0)


def test_enrich_stops_at_first_refusal(monkeypatch):
    import requests

    session = FakeSession([FakeResponse(403, "<html>captcha-delivery</html>"), FakeResponse(200, PAGE)])
    monkeypatch.setattr(requests, "Session", lambda: session)
    ads = [Ad(title=f"ThinkPad {i}", url=f"https://www.leboncoin.fr/vi/12345{i}.htm", source="leboncoin")
           for i in range(3)]
    stats = enrich.enrich(ads, {"sources": ["leboncoin"], "delay_seconds": 0})
    assert session.calls == 1 and stats["blocked"] == {"leboncoin": "HTTP 403"} and stats["enriched"] == 0


def test_enrich_success_and_filter_rerun(monkeypatch, config):
    import requests

    from dealsniper import filters

    oxidized = PAGE.replace("Batterie tient 2 h.", "Carte mère oxydée.")
    monkeypatch.setattr(requests, "Session", lambda: FakeSession([FakeResponse(200, oxidized)]))
    ad = Ad(title="Lenovo ThinkPad T480", url="https://www.leboncoin.fr/vi/1234567.htm", source="leboncoin", price=90)
    assert filters.check(ad, config) is None
    enrich.enrich([ad], {"sources": ["leboncoin"], "delay_seconds": 0})
    assert "oxyd" in filters.check(ad, config)
