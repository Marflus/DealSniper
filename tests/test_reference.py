from dealsniper import analyzer
from dealsniper.models import Ad
from dealsniper.reference import reference_price


def test_reference_ranges_by_model():
    assert reference_price("Steam Deck OLED 512 Go") == (380, 440)
    assert reference_price("Steam Deck Oled 1TB") == (450, 520)
    assert reference_price("Steam Deck LCD 512GB") == (250, 300)
    assert reference_price("MacBook Air M1 2020 8/256") == (380, 450)
    assert reference_price("Lenovo ThinkPad T480s i5") == (190, 240)
    # jeux, manettes, pièces : jamais le prix de l'appareil
    assert reference_price("Manette PS5 DualSense") is None
    assert reference_price("Clavier Lenovo ThinkPad T480") is None
    assert reference_price("Minecraft Nintendo Switch") is None


def test_config_lines_come_first():
    config = {"analyzer": {"reference_prices": [{"match": "steam deck oled", "low": 400, "high": 450},
                                                {"match": "mauvaise ligne"}]}}
    assert reference_price("Steam Deck OLED 512", config) == (400, 450)


def raw(resale):
    return {"type_affaire": "revente_directe", "score_rentabilite": 5, "facilite_reparation": 10,
            "facilite_vente": 8, "panne_probable": "aucune", "cout_piece_estime": 0,
            "valeur_revente_estimee": resale, "marge_nette_estimee": 0}


def test_same_model_gets_the_same_range_whatever_the_batch():
    """Steam Deck OLED 512 Go à 240 € : estimé 300 € dans un lot, 420 € dans un autre ; ramené dans 380-440 €."""
    ad = Ad(title="Steam Deck OLED 512 Go", url="https://www.vinted.fr/items/1-x", source="vinted", price=240)
    low = analyzer.make_realistic(raw(300), ad)
    high = analyzer.make_realistic(raw(420), ad)
    assert low["valeur_revente_estimee"] == 342 and low["source_revente"] == "repère"  # 380 × 0,9
    assert high["valeur_revente_estimee"] == 378 and high["source_revente"] == "ia"  # dans la fourchette
    assert "repère 380-440 €" in analyzer.format_ad(1, ad, 400)


def test_market_quote_wins_over_the_reference():
    ad = Ad(title="Steam Deck OLED 512 Go", url="https://www.vinted.fr/items/2-x", source="vinted", price=240,
            market={"median": 350, "count": 8, "model": "steam deck oled 512", "source": "annonces"})
    clean = analyzer.make_realistic(raw(420), ad)
    assert clean["valeur_revente_estimee"] == 315 and clean["source_revente"] == "cote"
    assert "repère" not in analyzer.format_ad(1, ad, 400)


def test_reference_never_lifts_an_unrecognised_accessory():
    """« Tige Dyson V15 » (30 €) estimée 25 € par l'IA : le repère d'un V15 (280-350 €) ne la fait pas monter."""
    ad = Ad(title="Rallonge Dyson V15", url="https://www.vinted.fr/items/3-x", source="vinted", price=30)
    clean = analyzer.make_realistic(raw(25), ad)
    assert clean["valeur_revente_estimee"] == 22.5 and clean["source_revente"] == "ia"
    over = analyzer.make_realistic(raw(500), ad)
    assert over["valeur_revente_estimee"] == 315  # vers le bas : toujours ramenée (350 × 0,9)


def test_recent_generations_and_parts_are_not_matched():
    assert reference_price("Lenovo ThinkPad T14 Gen 5 - In Garanzia") is None
    assert reference_price("Lenovo ThinkPad T14 Gen 1 i5") == (260, 340)
    assert reference_price("Moteur complet dyson v10 (BC10)") is None
