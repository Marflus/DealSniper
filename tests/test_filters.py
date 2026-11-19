from dealsniper.filters import apply_filters, check
from dealsniper.location import extract_location, foreign_country, is_overseas
from dealsniper.models import Ad


def ad(title, price=50.0, desc="", shipping=None, location=None, mode="fixe"):
    return Ad(title=title, url="https://example.com/" + title.replace(" ", "-"), source="test",
              price=price, shipping=shipping, description=desc, location=location, price_mode=mode)


def test_categories_and_signals(config):
    a = ad("Lenovo ThinkPad T480 sans SSD", 90)
    assert check(a, config) is None
    assert a.category == "PC portables pros" and "sans ssd" in a.signals
    b = ad("PC portable Asus Vivobook écran à changer", 60)
    assert check(b, config) is None and b.category == "PC portables grand public"
    assert check(ad("Canapé 3 places"), config) == "hors catégorie"


def test_budget_and_prices(config):
    assert check(ad("ThinkPad X1 Carbon", 175, shipping=10), config).startswith("hors budget")
    assert check(ad("Xbox Series S", 1), config).startswith("prix suspect")
    assert check(ad("Xbox Series S", 1, mode="enchere"), config) is None  # enchère : prix provisoire


def test_exclusions(config):
    assert "oxyde" in check(ad("PS4 Pro", 60, desc="Console OXYDÉE suite fuite"), config)
    assert "mot de passe bios" in check(ad("Dell Latitude 7490", 70, desc="Mot de passe BIOS oublié"), config)
    assert check(ad("Recherche PS5 HS", 50), config).startswith("annonce d'acheteur")
    assert "manette seule" in check(ad("Manette seule PS5", 20), config)
    assert check(ad("Nintendo Switch avec housse banane", 120), config) is None


def test_france_only(config):
    assert check(ad("ThinkPad T480", 90, desc="Lieu où se trouve l'objet : Allemagne"), config).startswith("hors de France")
    assert check(ad("ThinkPad T480", None, desc="Price £60, ships from United Kingdom"), config).startswith("hors de France")
    assert "DOM-TOM" in check(ad("ThinkPad T480", 90, location="Saint-Denis 97400"), config)
    assert check(ad("ThinkPad T480 made in China", 90, location="Lyon 69003"), config) is None


def test_location_helpers():
    assert extract_location("ThinkPad 95 € Lyon 69003 Aujourd'hui") == "Lyon 69003"
    assert foreign_country("Provenance : Chine") == "chine"
    assert foreign_country("Fabriqué en Chine, très bon état") is None
    assert is_overseas("Le Tampon 97430") and not is_overseas("Paris 75011")


def test_require_include_and_sorting(config):
    cfg = {**config, "categories": [{**config["categories"][0], "require_include": True}]}
    assert check(ad("Dell Latitude 7490", 90), cfg).startswith("aucun signal")
    assert check(ad("Dell Latitude 7490 cause départ", 90), cfg) is None
    kept, _ = apply_filters([ad("Dell Latitude 5490", 60), ad("Dell Latitude 5490 batterie HS", 80)], config)
    assert kept[0].title.endswith("batterie HS")
