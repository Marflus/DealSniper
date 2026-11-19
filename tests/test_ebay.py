import asyncio

import httpx

from dealsniper.http import PoliteClient
from dealsniper.sites import ebay

ITEM = {
    "title": "Lenovo ThinkPad T480 i5 sans SSD pour pièces",
    "price": {"value": "85.00", "currency": "EUR"},
    "itemWebUrl": "https://www.ebay.fr/itm/123456789012?hash=abc",
    "buyingOptions": ["FIXED_PRICE"],
    "condition": "Pour pièces détachées ou ne fonctionne pas",
    "shippingOptions": [{"shippingCost": {"value": "6.50", "currency": "EUR"}}],
    "itemLocation": {"postalCode": "69***", "country": "FR", "city": "Lyon"},
    "seller": {"feedbackPercentage": "99.2", "feedbackScore": 1534},
}
DETAILS = {"description": "<p>Écran parfait, vendu sans SSD ni chargeur.</p>",
           "condition": "Pour pièces", "conditionDescription": "Ne démarre pas sans disque",
           "itemLocation": {"city": "Lyon", "postalCode": "69003"}}


def test_filter_and_conversion():
    f = ebay.build_filter({"max_price": 120, "condition": "parts"})
    assert "itemLocationCountry:FR" in f and "price:[0..120]" in f and "conditionIds:{7000}" in f
    ad = ebay.item_to_ad(ITEM)
    assert ad.uid == "ebay:123456789012" and ad.price == 85.0 and ad.shipping == 6.5
    assert ad.location == "Lyon 69***" and ad.seller == {"positive_pct": 99.2, "reviews": 1534}
    auction = ebay.item_to_ad({**ITEM, "buyingOptions": ["AUCTION"], "currentBidPrice": {"value": "12", "currency": "EUR"}})
    assert auction.price == 12.0 and auction.price_mode == "enchere"


def test_site_two_steps(monkeypatch, config):
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
        if request.url.path.endswith("/search"):
            assert request.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_FR"
            assert request.url.params["sort"] == "newlyListed"
            return httpx.Response(200, json={"itemSummaries": [ITEM]})
        assert request.url.params["legacy_item_id"] == "123456789012"
        return httpx.Response(200, json=DETAILS)

    site = ebay.EbaySite({**config["sites"]["ebay"], "searches": [{"query": "thinkpad", "max_price": 120}]}, config)
    assert site.is_configured()

    async def go():
        async with PoliteClient("t", 0, transport=httpx.MockTransport(handler)) as http:
            (ad,) = await site.list_recent(http)
            return await site.fetch_detail(http, ad)

    ad = asyncio.run(go())
    assert "vendu sans SSD" in ad.description and ad.description.startswith("État : Pour pièces — Ne démarre pas")
    assert seen.count("/identity/v1/oauth2/token") == 1  # jeton réutilisé


def test_not_configured_without_keys(config):
    assert not ebay.EbaySite(config["sites"]["ebay"], config).is_configured()
