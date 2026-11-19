from dealsniper.filters import apply_filters, check
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
    assert check(ad("ThinkPad T480", 90, desc="Lieu où se trouve l'objet : Allemagne"), config).startswith("hors de France")
    assert "DOM-TOM" in check(ad("ThinkPad T480", 90, location="Saint-Denis 97400"), config)


def test_sorting_prioritizes_signals(config):
    kept, _ = apply_filters([ad("Dell Latitude 5490", 60), ad("Dell Latitude 5490 batterie HS", 80)], config)
    assert kept[0].title.endswith("batterie HS")


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
