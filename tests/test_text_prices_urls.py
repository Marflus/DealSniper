import pytest

from dealsniper.prices import detect_price_mode, extract_price, extract_shipping, has_foreign_currency, parse_amount
from dealsniper.text import clean_html, contains_keyword, normalize, truncate
from dealsniper.urls import ad_uid, canonical_url, unwrap_tracking_url


@pytest.mark.parametrize("raw,expected", [
    ("45", 45.0), ("45,90", 45.9), ("1 234,56", 1234.56), ("1.234,56", 1234.56),
    ("1.234", 1234.0), ("45.00", 45.0), ("abc", None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_price_and_shipping():
    assert extract_price("Livraison 6,50 € — ThinkPad T480 à 89 €") == 89.0
    assert extract_price("EUR 120,00 + EUR 9,99 de frais de livraison") == 120.0
    assert extract_price("PS4 slim surchauffe 40 € Livraison 5,50 €") == 40.0
    assert extract_shipping("PS4 slim surchauffe 40 € Livraison 5,50 €") == 5.5
    assert extract_shipping("+ 4,99 € de livraison") == 4.99
    assert extract_shipping("Livraison gratuite") == 0.0
    assert extract_shipping("Remise en main propre") is None


def test_price_mode_and_currency():
    assert detect_price_mode("Enchère en cours : 12 €") == "enchere"
    assert detect_price_mode("Achat immédiat 45 €") == "fixe"
    assert has_foreign_currency("Price: £45.00")
    assert not has_foreign_currency("45 € (≈ 50 $)")


def test_text_helpers():
    assert clean_html("<p>Carte <b>mère</b>&nbsp;OK<script>x()</script></p>") == "Carte mère OK"
    assert normalize("Oxydé  TOMBÉ") == "oxyde tombe"
    assert contains_keyword("console oxydee", "oxydé")
    assert not contains_keyword("hstore", "hs")
    assert truncate("mot " * 300, 600).endswith("…")


def test_urls():
    assert ad_uid("https://www.leboncoin.fr/ad/ordinateurs/2512345678?utm_source=mail") == "lbc:2512345678"
    assert canonical_url("https://x.fr/a/?utm_source=1&id=3#top") == "https://x.fr/a?id=3"
    rover = "https://rover.ebay.com/rover/0/0/0?mpre=https%3A%2F%2Fwww.ebay.fr%2Fitm%2F123456789012"
    assert ad_uid(unwrap_tracking_url(rover)) == "ebay:123456789012"
    # Lien de suivi opaque : identifiant titre + prix, stable d'un email à l'autre.
    a = ad_uid("https://t.example.com/abc", "ThinkPad", 90, url_is_stable=False)
    b = ad_uid("https://t.example.com/xyz", "ThinkPad", 90, url_is_stable=False)
    assert a == b
