from dealsniper.filters import check, priority
from dealsniper.models import Ad


def ad(title, price=50.0, desc="", shipping=None, location=None, mode="fixe"):
    return Ad(title=title, url="https://example.com/" + title.replace(" ", "-"), source="test",
              price=price, shipping=shipping, description=desc, location=location, price_mode=mode)


def test_categories_and_signals(config):
    a = ad("Lenovo ThinkPad T480 sans SSD", 90)
    assert check(a, config) is None and a.category == "PC portables pros" and "sans ssd" in a.signals
    assert check(ad("Canapé 3 places"), config) == "hors catégorie"


def test_budget_exclusions_and_france(config):
    assert check(ad("ThinkPad X1 Carbon", 175, shipping=10), config).startswith("hors budget")
    assert check(ad("Xbox Series S", 1), config).startswith("prix suspect")
    assert check(ad("Xbox Series S", 1, mode="enchere"), config) is None
    assert "oxyde" in check(ad("PS4 Pro", 60, desc="Console OXYDÉE"), config)
    assert check(ad("Recherche PS5 HS", 50), config).startswith("annonce d'acheteur")
    abroad = ad("ThinkPad T480", 90, desc="Lieu où se trouve l'objet : Allemagne")
    assert check(abroad, config).startswith("hors de France")
    assert "DOM-TOM" in check(ad("ThinkPad T480", 90, location="Saint-Denis 97400"), config)


def test_sorting_prioritizes_signals(config):
    ads = [ad("Dell Latitude 5490", 60), ad("Dell Latitude 5490 batterie HS", 80)]
    assert all(check(a, config) is None for a in ads)
    assert sorted(ads, key=priority)[0].title.endswith("batterie HS")


def test_sold_or_reserved_ads_are_rejected(config):
    from dealsniper.filters import sold_problem

    f = config["filters"]
    def ad(title, description="", available=True):
        return Ad(title=title, url="https://e.x/1", source="t", price=50, description=description, available=available)

    assert sold_problem(ad("PS5 Slim - VENDUE"), f)
    assert sold_problem(ad("RÉSERVÉ iPhone 12"), f)
    assert sold_problem(ad("ThinkPad T480", "Déjà vendu, désolé"), f)
    assert sold_problem(ad("MacBook Air", "Vente en cours avec un acheteur"), f)
    assert sold_problem(ad("Switch", available=False), f)
    assert not sold_problem(ad("iPhone 11 vendu pour pièces"), f)
    assert not sold_problem(ad("Écran soldé", "vendu avec son câble"), f)


def test_desirable_brand_signal(config):
    from dealsniper.filters import desirable_brand
    from dealsniper.market import Quote, free_estimate

    a = ad("Lenovo ThinkPad T480 sans SSD", 90)
    assert check(a, config) is None and desirable_brand(a) == "thinkpad"
    assert any(s.startswith("marque recherchée") for s in a.signals)
    # La marque seule ne remplace pas un signal exigé par la catégorie
    config["filters"]["desirable_brands"] = ["nobrand"]
    b = ad("Lenovo ThinkPad T480 sans SSD", 90)
    check(b, config)
    assert desirable_brand(b) is None
    # Mode sans IA : +1 au score pour une marque recherchée rentable
    quote = Quote(model="thinkpad t480", median=200, count=5, source="test")
    a.price_mode, b.price_mode = "fixe", "fixe"
    assert free_estimate(a, quote)["score_rentabilite"] == free_estimate(b, quote)["score_rentabilite"] + 1


def test_eliminatory_delivery_and_max_price(config):
    config["filters"].update(delivery="ship", max_price=100, drop_uncategorized=False)
    assert check(ad("Raspberry Pi 4", 40), config) == "pas de livraison"
    assert check(ad("Raspberry Pi 4", 40, shipping=5), config) is None
    assert "prix max" in check(ad("Raspberry Pi 5 kit", 98, shipping=6), config)


def test_seller_type_is_eliminatory(config):
    config["filters"].update(seller_type="particulier", drop_uncategorized=False)
    pro = ad("Ski Rossignol", 80)
    pro.seller = {"type": "pro"}
    unknown = ad("Ski Dynastar", 80)
    assert check(pro, config) == "vendeur professionnel"
    assert check(unknown, config) is None  # type inconnu : gardée


def test_desirable_brands_avoid_generic_words():
    from dealsniper.config import DESIRABLE_BRANDS
    from dealsniper.utils.text import first_keyword, normalize

    found = {t: first_keyword(normalize(t), DESIRABLE_BRANDS) for t in (
        "Plan de travail surface 2m", "Tapis pixel art", "Télé OLED 55 Hisense", "Framework carton",
        "Microsoft Surface Pro 7", "Google Pixel 7 Pro", "Dell Latitude 7490", "Enceinte Bang & Olufsen A1")}
    assert found == {"Plan de travail surface 2m": None, "Tapis pixel art": None, "Télé OLED 55 Hisense": None,
                     "Framework carton": None, "Microsoft Surface Pro 7": "microsoft surface",
                     "Google Pixel 7 Pro": "google pixel", "Dell Latitude 7490": "dell latitude",
                     "Enceinte Bang & Olufsen A1": "bang & olufsen"}


def test_vinted_counts_as_shippable(config):
    config["filters"].update(delivery="ship", drop_uncategorized=False)
    vinted = Ad(title="Chaussures blanches 43", url="https://www.vinted.fr/items/1", source="vinted", price=30)
    lbc = Ad(title="Chaussures blanches 43", url="https://www.leboncoin.fr/ad/x/1", source="leboncoin", price=30)
    assert vinted.shippable and check(vinted, config) is None  # Vinted : envoi toujours possible
    assert check(lbc, config) == "pas de livraison"
    assert vinted.to_dict()["shippable"] is True
