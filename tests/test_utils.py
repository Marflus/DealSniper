import pytest

from dealsniper.utils.location import extract_location, foreign_country, is_overseas
from dealsniper.utils.prices import (
    detect_price_mode,
    extract_price,
    extract_shipping,
    has_foreign_currency,
    parse_amount,
)
from dealsniper.utils.text import clean_html, contains_keyword, normalize, truncate
from dealsniper.utils.urls import ad_uid, canonical_url, unwrap_tracking_url


@pytest.mark.parametrize("raw,expected", [
    ("45", 45.0), ("45,90", 45.9), ("1 234,56", 1234.56), ("1.234,56", 1234.56), ("1.234", 1234.0), ("abc", None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_prices():
    assert extract_price("Livraison 6,50 € — ThinkPad T480 à 89 €") == 89.0
    assert extract_price("MacBook Air Retina 13 2020 255 € Cahors 46000") == 255.0  # « 2020 » n'est pas collé au prix
    assert extract_shipping("PS4 slim 40 € Livraison 5,50 €") == 5.5
    assert extract_shipping("Livraison gratuite") == 0.0
    assert detect_price_mode("Enchère en cours : 12 €") == "enchere"
    assert has_foreign_currency("Price: £45.00")


def test_text():
    assert clean_html("<p>Carte <b>mère</b>&nbsp;OK<script>x()</script></p>") == "Carte mère OK"
    assert normalize("Oxydé  TOMBÉ") == "oxyde tombe"
    assert contains_keyword("console oxydee", "oxydé") and not contains_keyword("hstore", "hs")
    assert truncate("mot " * 300, 600).endswith("…")


def test_urls():
    assert ad_uid("https://www.ebay.fr/itm/123456789012?hash=x") == "ebay:123456789012"
    assert ad_uid("https://www.leboncoin.fr/vi/3312499828.htm") == "lbc:3312499828"
    assert canonical_url("https://x.fr/a/?utm_source=1&id=3#top") == "https://x.fr/a?id=3"
    rover = "https://rover.ebay.com/rover/0/0/0?mpre=https%3A%2F%2Fwww.ebay.fr%2Fitm%2F123456789012"
    assert ad_uid(unwrap_tracking_url(rover)) == "ebay:123456789012"


def test_location():
    assert extract_location("ThinkPad 95 € Lyon 69003 Aujourd'hui") == "Lyon 69003"
    assert foreign_country("Provenance : Chine") == "chine"
    assert foreign_country("Fabriqué en Chine, très bon état") is None
    assert is_overseas("Le Tampon 97430") and not is_overseas("Paris 75011")
