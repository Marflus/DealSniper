import pytest

from dealsniper.utils.location import foreign_country, is_overseas
from dealsniper.utils.prices import extract_price, has_foreign_currency, parse_amount
from dealsniper.utils.text import clean_html, contains_keyword, normalize, truncate
from dealsniper.utils.urls import ad_uid, canonical_url


@pytest.mark.parametrize("raw,expected", [
    ("45", 45.0), ("45,90", 45.9), ("1 234,56", 1234.56), ("1.234,56", 1234.56), ("1.234", 1234.0), ("abc", None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_prices():
    assert extract_price("Livraison 6,50 € — ThinkPad T480 à 89 €") == 89.0
    assert extract_price("MacBook Air Retina 13 2020 255 € Cahors 46000") == 255.0  # « 2020 » n'est pas collé au prix
    assert extract_price("PS4 slim 40 € Livraison 5,50 €") == 40.0  # les frais de port ne sont pas le prix
    assert has_foreign_currency("Price: £45.00")


def test_text():
    assert clean_html("<p>Carte <b>mère</b>&nbsp;OK<script>x()</script></p>") == "Carte mère OK"
    assert normalize("Oxydé  TOMBÉ") == "oxyde tombe"
    assert contains_keyword("console oxydee", "oxydé") and not contains_keyword("hstore", "hs")
    assert truncate("mot " * 300, 600).endswith("…")


def test_urls():
    assert ad_uid("https://www.ebay.fr/itm/123456789012?hash=x") == "ebay:123456789012"
    assert canonical_url("https://x.fr/a/?utm_source=1&id=3#top") == "https://x.fr/a?id=3"
    assert ad_uid("https://x.fr/a?utm_source=1") == ad_uid("https://x.fr/a")


def test_location():
    assert foreign_country("Provenance : Chine") == "chine"
    assert foreign_country("Fabriqué en Chine, très bon état") is None
    assert is_overseas("Le Tampon 97430") and not is_overseas("Paris 75011")


def test_fast_keyword_search_matches_the_simple_definition():
    """first_keyword (recherche combinée + caches) doit donner exactement le résultat de la définition simple."""
    import random
    import re
    import unicodedata

    from dealsniper.utils.text import first_keyword, normalize

    def slow_normalize(text):
        text = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in text if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", text.replace("’", "'").replace(" ", " ").lower()).strip()

    def slow_first(text, keywords):
        for kw in keywords:
            k = slow_normalize(kw)
            if not k:
                continue
            suffix = r"(?:e|s|es|x)?" if k[-1].isalpha() else ""
            if re.search(r"(?<![a-z0-9])" + re.escape(k) + suffix + r"(?![a-z0-9])", text):
                return kw
        return None

    words = ["oxyde", "HS", "carte mère", "écran cassé", "i5", "ps5", "boîte vide", "Dyson V10", "sans ssd",
             "ne s'allume", "a saisir", "Œuvre", "x", "", "8go"]
    texts = ["Carte Mère HS", "écran cassée, oxydées", "PS5 + manettes", "hstore i5-8250U", "dyson v10s neuf",
             "Sans SSD ni RAM", "ne s’allume plus", "À SAISIR !", "œuvre d'art", "boite vide 8go", "rien à signaler"]
    rng = random.Random(4)
    for _ in range(2000):
        text = " ".join(rng.sample(texts, rng.randint(1, 3)))
        keywords = rng.sample(words, rng.randint(1, 6))
        assert normalize(text) == slow_normalize(text)
        assert first_keyword(normalize(text), keywords) == slow_first(slow_normalize(text), keywords), (text, keywords)
