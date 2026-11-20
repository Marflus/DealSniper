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
    "image": {"imageUrl": "https://i.ebayimg.com/images/g/abc/s-l500.jpg"},
}
DETAILS = {"description": "<p>Écran parfait, vendu sans SSD ni chargeur.</p>",
           "condition": "Pour pièces", "conditionDescription": "Ne démarre pas sans disque",
           "itemLocation": {"city": "Lyon", "postalCode": "69003"}}


def test_filter_and_conversion():
    f = ebay.build_filter({"max_price": 120, "condition": "parts"})
    assert "itemLocationCountry:FR" in f and "price:[0..120]" in f and "conditionIds:{7000}" in f
    ad = ebay.item_to_ad(ITEM)
    assert ad.uid == "ebay:123456789012" and ad.price == 85.0 and ad.shipping == 6.5
    assert ad.location == "Lyon 69***" and ad.seller == {"name": None, "positive_pct": 99.2, "reviews": 1534}
    assert ad.image == "https://i.ebayimg.com/images/g/abc/s-l500.jpg"
    bid = {"value": "12", "currency": "EUR"}
    auction = ebay.item_to_ad({**ITEM, "buyingOptions": ["AUCTION"], "currentBidPrice": bid})
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


def test_auction_with_best_offer_and_missing_id(config):
    ad = ebay.item_to_ad({**ITEM, "buyingOptions": ["AUCTION", "BEST_OFFER"],
                          "currentBidPrice": {"value": "20", "currency": "EUR"}})
    assert ad.price_mode == "enchere" and ad.price == 20.0

    async def go():
        async with PoliteClient("t", 0, transport=httpx.MockTransport(lambda r: httpx.Response(500))) as http:
            site = ebay.EbaySite(config["sites"]["ebay"], config)
            no_id = ebay.item_to_ad({**ITEM, "itemWebUrl": "https://exemple.fr/annonce"})
            return await site.fetch_detail(http, no_id)

    assert asyncio.run(go()).url == "https://exemple.fr/annonce"  # aucune requête envoyée


def test_search_paginates_and_deduplicates(config):
    import httpx

    from dealsniper.http import PoliteClient
    from dealsniper.sites import ebay

    def item(i):
        return {"title": f"ThinkPad {i}", "itemWebUrl": f"https://www.ebay.fr/itm/{i}",
                "price": {"value": "50", "currency": "EUR"}, "buyingOptions": ["FIXED_PRICE"]}

    calls = []

    def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
        offset = int(request.url.params.get("offset", 0))
        calls.append(offset)
        if offset == 0:
            return httpx.Response(200, json={"itemSummaries": [item(i) for i in range(200)]})
        return httpx.Response(200, json={"itemSummaries": [item(i) for i in range(190, 240)]})  # recoupe 190-199

    site = ebay.EbaySite({**config["sites"]["ebay"], "limit": 300,
                          "searches": [{"query": "thinkpad", "max_price": 120}]}, config)

    async def go():
        async with PoliteClient("t", 0, transport=httpx.MockTransport(handler)) as http:
            return await site.list_recent(http)

    ads = asyncio.run(go())
    assert calls == [0, 200]              # deux pages demandées (limit 300)
    assert len(ads) == 240                # 200 + 50 − 10 doublons (190-199)
    assert len({a.uid for a in ads}) == 240
