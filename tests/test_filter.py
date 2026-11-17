from core import Ad, load_config
from filter import apply_filters, check

CFG = load_config()


def ad(title, price=50.0, desc="", shipping=None):
    return Ad(title=title, url="https://example.com/" + title.replace(" ", "-"), source="test",
              price=price, shipping=shipping, description=desc)


def test_categorization_and_signals():
    a = ad("Lenovo ThinkPad T480 sans SSD", 90)
    assert check(a, CFG) is None
    assert a.category == "PC portables pros"
    assert "sans ssd" in a.signals


def test_reject_uncategorized():
    assert check(ad("Canapé 3 places"), CFG) == "hors catégorie"


def test_reject_over_budget_including_shipping():
    reason = check(ad("ThinkPad X1 Carbon", 175, shipping=10), CFG)
    assert reason and reason.startswith("hors budget")


def test_reject_excluded_keywords_accent_insensitive():
    assert "oxyde" in check(ad("PS4 Pro", 60, desc="Console OXYDÉE suite fuite"), CFG)
    assert "mot de passe bios" in check(ad("Dell Latitude 7490", 70, desc="Mot de passe BIOS oublié"), CFG)


def test_reject_buyer_ads_and_suspicious_price():
    assert check(ad("Recherche PS5 HS", 50), CFG).startswith("annonce d'acheteur")
    assert check(ad("Xbox Series S", 1), CFG).startswith("prix suspect")


def test_word_boundaries():
    # "ban" (exclusion consoles) ne doit pas rejeter "banane" ni "turban"
    assert check(ad("Nintendo Switch avec housse banane", 120), CFG) is None


def test_sort_prioritizes_signals():
    kept, rejected = apply_filters([ad("Dell Latitude 5490", 60), ad("Dell Latitude 5490 batterie HS", 80)], CFG)
    assert [k.title for k in kept][0].endswith("batterie HS")
    assert not rejected


def test_deal_signals_for_working_items():
    a = ad("PS5 édition standard comme neuve, vente urgente déménagement", 180)
    assert check(a, CFG) is None
    assert {"urgent", "demenagement"} <= set(a.signals)


def test_require_include_accepts_deal_signals():
    cfg = {**CFG, "categories": [{**CFG["categories"][0], "require_include": True}]}
    assert check(ad("Dell Latitude 7490", 90), cfg).startswith("aucun signal")
    assert check(ad("Dell Latitude 7490 cause départ", 90), cfg) is None
