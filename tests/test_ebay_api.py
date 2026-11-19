from dealsniper.sources import ebay_api

ITEM = {
    "itemId": "v1|123456789012|0",
    "title": "Lenovo ThinkPad T480 i5 sans SSD pour pièces",
    "price": {"value": "85.00", "currency": "EUR"},
    "itemWebUrl": "https://www.ebay.fr/itm/123456789012?hash=abc",
    "buyingOptions": ["FIXED_PRICE"],
    "condition": "Pour pièces détachées ou ne fonctionne pas",
    "shippingOptions": [{"shippingCost": {"value": "6.50", "currency": "EUR"}}],
    "itemLocation": {"postalCode": "69***", "country": "FR", "city": "Lyon"},
}


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeSession:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def post(self, url, **kw):
        return FakeResponse({"access_token": "tok"})

    def get(self, url, **kw):
        self.calls.append(kw)
        return FakeResponse({"itemSummaries": [ITEM, {"title": "sans url"}]})


def test_filter_is_france_only_with_price_and_condition():
    f = ebay_api.build_filter({"max_price": 120, "condition": "parts"}, {"country": "FR"})
    assert "itemLocationCountry:FR" in f and "price:[0..120]" in f and "conditionIds:{7000}" in f


def test_item_to_ad():
    ad = ebay_api.item_to_ad(ITEM, "thinkpad")
    assert ad.uid == "ebay:123456789012" and ad.price == 85.0 and ad.shipping == 6.5
    assert ad.location == "Lyon 69***" and "pour pièces" in ad.description.lower()
    auction = ebay_api.item_to_ad({**ITEM, "buyingOptions": ["AUCTION"],
                                   "currentBidPrice": {"value": "12.00", "currency": "EUR"}}, "x")
    assert auction.price == 12.0 and auction.price_mode == "enchere"


def test_collect(monkeypatch):
    import requests

    session = FakeSession()
    monkeypatch.setattr(requests, "Session", lambda: session)
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    ads, report = ebay_api.collect({"searches": [{"query": "thinkpad", "condition": "parts", "max_price": 120}]})
    assert len(ads) == 1 and report.ok and report.items == 2
    assert session.calls[0]["headers"]["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_FR"
    assert session.calls[0]["params"]["sort"] == "newlyListed"


def test_not_configured_is_silently_skipped(monkeypatch):
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    ads, report = ebay_api.collect({"searches": [{"query": "x"}]})
    assert ads == [] and report.ok
