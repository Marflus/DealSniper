import asyncio

import pytest

from dealsniper.db import Database
from dealsniper.market import Market, central_median, is_the_device, is_working, model_key
from dealsniper.models import Ad

BRANDS = ["thinkpad", "latitude", "elitebook", "macbook pro", "macbook air", "dyson", "sonos", "bose", "surface pro",
          "thinkcentre", "m720q", "makita", "pc portable", "tv"]


@pytest.mark.parametrize("title, key", [
    ("Lenovo ThinkPad T480 i5 8Go sans SSD", "thinkpad t480"),
    ('Dell Latitude 7490 14" i5', "latitude 7490"),
    ('MacBook Pro 13" 2017 - i5 - 8Go - 256Go', "macbook pro 2017"),
    ("MacBook Air M1 2020 8Go", "macbook air m1"),
    ("HP EliteBook 840 G5", "elitebook 840 g5"),
    ("Microsoft Surface Pro 7", "surface pro 7"),
    ("Bose QuietComfort 35 II", "bose quietcomfort 35"),
    ("Sonos One SL", "sonos one"),
    ("Lenovo ThinkCentre M720q Tiny", "thinkcentre m720q"),
    ("PC portable Asus 15 pouces", None),
    ("TV Samsung 55 pouces", None),
])
def test_model_key(title, key):
    assert model_key(title, BRANDS) == key


def test_working_and_device_detection():
    assert is_working("ThinkPad T480 très bon état") and not is_working("ThinkPad T480 HS ne s'allume plus")
    assert not is_working("Dyson V10 batterie HS") and not is_working("ThinkPad T480 sans SSD")
    assert not is_the_device("Chargeur ThinkPad T480 65W") and not is_the_device("Lot de 3 ThinkPad T480")
    assert is_the_device("ThinkPad T480 avec chargeur")


def test_central_median_ignores_outliers():
    assert central_median([150, 160, 170]) is None  # pas assez d'annonces
    assert central_median([10, 150, 160, 170, 180, 190, 2000]) == 170


def make_ad(i, title, price):
    return Ad(title=title, url=f"https://e.x/{i}", source="t", price=price)


def test_quote_from_seen_ads_then_from_ebay_sample(tmp_path):
    config = {"categories": [{"keywords": ["thinkpad", "dyson"]}], "filters": {"min_price": 5}}

    async def scenario():
        db = Database(tmp_path / "m.db")
        calls = []

        async def sampler(query):
            calls.append(query)
            return [(f"Dyson V10 Absolute {i}", p) for i, p in enumerate([150, 160, 170, 180, 190])] + [
                ("Dyson V10 batterie HS", 40), ("Batterie Dyson V10", 30)]  # ignorés : en panne, accessoire

        market = Market(db, config, sampler)
        seen = [make_ad(i, "ThinkPad T480 bon état", p) for i, p in enumerate([200, 210, 220, 230, 240])]
        seen.append(make_ad(9, "ThinkPad T480 HS", 50))  # en panne : pas dans la cote
        await market.observe(seen)
        await market.observe(seen)  # une même annonce n'est comptée qu'une fois
        t480 = await market.quote(make_ad(20, "ThinkPad T480 sans SSD", 90))
        v10 = await market.quote(make_ad(21, "Dyson V10 HS", 40))
        again = await market.quote(make_ad(22, "Dyson V10 moteur HS", 30))
        db.close()
        return t480, v10, again, calls

    t480, v10, again, calls = asyncio.run(scenario())
    assert (t480.median, t480.count, t480.source) == (220, 5, "annonces")
    assert (v10.median, v10.count, v10.source) == (170, 5, "ebay")
    assert again.median == 170 and calls == ["dyson v10"]  # échantillon demandé une seule fois
