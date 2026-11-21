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
    assert central_median([10, 150, 160, 170, 180, 190, 2000]) == 175  # 10 € écarté (moins du tiers)


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


def test_price_history_monthly_median(tmp_path):
    import asyncio

    from dealsniper.db import Database

    async def scenario():
        db = Database(tmp_path / "h.db")
        rows = [(f"u{i}", "thinkpad t480", p, 1, "ebay") for i, p in enumerate([180, 200, 220])]
        await db.record_prices(rows)
        hist = db.price_history("thinkpad t480")
        db.close()
        return hist

    hist = asyncio.run(scenario())
    assert len(hist) == 1 and hist[0]["prix"] == 200 and hist[0]["count"] == 3  # médiane du mois


def test_game_or_accessory_is_not_the_console(config):
    """« Zelda … NINTENDO Switch » vend un jeu : ni cote de console, ni prix de console pour l'IA."""
    from dealsniper.filters import check
    from dealsniper.market import Market, is_the_device
    from dealsniper.models import Ad
    from dealsniper.utils.products import game_or_accessory

    games = ["The legend of Zelda Echoes of Wisdom NINTENDO Switch", "Manette PS5 DualSense", "FIFA 24 PS4",
             "Super Mario Odyssey Switch", "Jeu Switch Pokemon Violet", "Coque Switch OLED", "Pokémon Jaune Game Boy"]
    consoles = ["Nintendo Switch OLED + 3 jeux", "PS5 digital + manette", "Console PS4 slim 500 Go",
                "Lot Nintendo Switch + Mario Kart", "Sony PlayStation 5 slim", "Steam Deck OLED 512",
                "Switch Lite turquoise", "Game Boy Color jaune", "Xbox Series X 1To",
                "Bonjour je vend ma ps4 marche très bien"]
    others = ["Carte graphique RTX 3080", "Casque Sony WH-1000XM4", "Mario Kart 8 Deluxe"]
    assert all(game_or_accessory(t) for t in games)
    assert not any(game_or_accessory(t) for t in consoles + others)

    zelda = Ad(title=games[0], url="https://www.leboncoin.fr/ad/x/1", source="leboncoin", price=35)
    assert not is_the_device(zelda.title)  # son prix ne fausse pas la cote des consoles
    config["categories"].append({"name": "Consoles", "keywords": ["switch", "ps5"], "max_budget": 300})
    config["filters"]["drop_uncategorized"] = False
    assert Market(db=None, config=config).key(zelda) is None  # pas de cote de console
    check(zelda, config)
    assert any("jeu ou accessoire" in s for s in zelda.signals)  # transmis à l'IA


def test_spare_part_is_flagged_for_the_ai(config):
    """« Ventilateur ThinkPad T480 » (6 €) était estimé au prix d'un ThinkPad réparé (135 €)."""
    from dealsniper.filters import check
    from dealsniper.models import Ad
    from dealsniper.utils.products import part_or_accessory

    parts = ["Ventilateur / Dissipateur Lenovo ThinkPad T480", "Carte Mère Thinkpad T480",
             "Clavier Lenovo ThinkPad T470 T480 Azerty", "Chargeur seul Dyson", "Palmrest Lenovo ThinkPad L14"]
    devices = ["Lenovo ThinkPad T480 i5 8Go", "Dyson V10 batterie HS", "Lot de 3 ThinkPad X280",
               "Écran Samsung 27 pouces", "console Nintendo NES seule sans câbles"]
    assert all(part_or_accessory(t) for t in parts)
    assert not any(part_or_accessory(t) for t in devices)

    ad = Ad(title=parts[0], url="https://www.vinted.fr/items/1-x", source="vinted", price=6)
    config["filters"]["drop_uncategorized"] = False
    config["filters"]["min_price"] = 0
    check(ad, config)
    assert any("pièce ou accessoire" in s for s in ad.signals)


def test_one_off_search_drops_off_topic_results(config):
    """Recherche ponctuelle : Vinted renvoie aussi des articles voisins (t-shirts pour « thinkpad t480 »)."""
    from dealsniper.filters import check
    from dealsniper.models import Ad
    from dealsniper.utils.products import query_words

    config["filters"].update(drop_uncategorized=False, min_price=0, ignore_category_budget=True,
                             query_words=query_words("thinkpad t480"))
    shirt = Ad(title="YFG Pave T-Shirt Rapper", url="https://www.vinted.fr/items/2-x", source="vinted", price=9)
    laptop = Ad(title="Lenovo T480s i5", url="https://www.vinted.fr/items/3-x", source="vinted", price=150)
    assert check(shirt, config) == "hors sujet (aucun mot de la recherche)"
    assert check(laptop, config) is None

    config["filters"]["query_words"] = query_words("chaussures blanches taille 43")
    shoes = Ad(title="Des baskets", url="https://www.vinted.fr/items/4-x", source="vinted", price=15,
               description="Taille : 43")
    polo = Ad(title="Polo homme", url="https://www.vinted.fr/items/5-x", source="vinted", price=5,
              description="Taille : XL")
    assert check(shoes, config) is None  # la taille de la carte Vinted suffit
    assert check(polo, config) == "hors sujet (aucun mot de la recherche)"  # « Taille » seul ne compte pas


def test_vinted_card_details_are_kept():
    from dealsniper.sites.vinted import _clean_vinted_title, _vinted_details

    raw = ("Lenovo ThinkPad T480 | i7, 16GB, Marque: Lenovo, Modèle: ThinkPad T480, État: Très bon état, "
           "Taille: 14, 279.00 €, 293.65 €")
    assert _clean_vinted_title(raw) == "Lenovo ThinkPad T480 | i7, 16GB"
    assert _vinted_details(raw) == "Marque : Lenovo | Modèle : ThinkPad T480 | État : Très bon état | Taille : 14"
    assert _vinted_details("Des baskets") == ""


def test_accessories_do_not_pollute_the_quote():
    """MacBook Air M1 : 6 housses, boîtes et hubs à 5-15 € donnaient une cote de 139 € (vraies M1 écartées)."""
    from dealsniper.market import central_median, is_the_device, is_working

    junk = ["Boite Macbook Air 13\" M1", "Hub USB-C multipuerto Compatible MacBook Air M1",
            "Finite Case for MacBook Air 13.3", "BlueSwan Custodia Macbook Air 13",
            "Top Case et carte mère MacBook Air"]
    assert not any(is_the_device(t) for t in junk)
    assert is_the_device("MacBook Air M1 2020") and is_the_device("Top état MacBook Air M1")
    assert not is_working("MacBook Air M1 écran noir quand on l'incline")
    prices = [5, 8, 8.9, 12, 15, 15, 139, 150, 200, 290, 300, 340, 350, 350]
    assert central_median(prices) > 250  # les prix sans rapport ne tirent plus la cote vers le bas
    assert central_median([200, 210, 220, 230, 240]) == 220
