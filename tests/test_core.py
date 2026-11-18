import pytest

from core import ad_uid, canonical_url, clean_html, extract_price, extract_shipping, normalize, parse_amount, truncate


@pytest.mark.parametrize("raw,expected", [
    ("45", 45.0), ("45,90", 45.9), ("1 234,56", 1234.56), ("1.234,56", 1234.56),
    ("1.234", 1234.0), ("45.00", 45.0), ("abc", None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_extract_price_ignores_shipping():
    assert extract_price("Livraison 6,50 € — ThinkPad T480 à 89 €") == 89.0
    assert extract_price("EUR 120,00 + EUR 9,99 shipping") == 120.0
    assert extract_price("pas de prix") is None


def test_extract_shipping():
    assert extract_shipping("Frais de port : 7,90 €") == 7.9
    assert extract_shipping("+ 4,99 € de livraison") == 4.99
    assert extract_shipping("Livraison gratuite") == 0.0
    assert extract_shipping("Remise en main propre") is None


def test_clean_html_and_normalize():
    assert clean_html("<p>Carte <b>mère</b>&nbsp;OK<script>x()</script></p>") == "Carte mère OK"
    assert normalize("Oxydé  TOMBÉ") == "oxyde tombe"


def test_truncate():
    assert truncate("a" * 10, 20) == "a" * 10
    out = truncate("mot " * 300, 600)
    assert len(out) <= 601 and out.endswith("…")


def test_uid_is_stable_across_tracking_params():
    a = ad_uid("https://www.leboncoin.fr/ad/ordinateurs/2512345678?utm_source=mail")
    b = ad_uid("https://www.leboncoin.fr/ad/ordinateurs/2512345678")
    assert a == b == "lbc:2512345678"
    assert canonical_url("https://x.fr/a/?utm_source=1&id=3#top") == "https://x.fr/a?id=3"


def test_unwrap_ebay_rover_redirect():
    from core import unwrap_tracking_url
    url = "https://rover.ebay.com/rover/0/0/0?mpre=https%3A%2F%2Fwww.ebay.fr%2Fitm%2F123456789012%3Fhash%3Dx"
    assert unwrap_tracking_url(url) == "https://www.ebay.fr/itm/123456789012?hash=x"
    assert ad_uid(unwrap_tracking_url(url)) == "ebay:123456789012"
