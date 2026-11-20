from dealsniper.geo import Geo, distance_km

COMMUNES = [
    {"nom": "Lyon", "codesPostaux": ["69001", "69003"], "centre": {"coordinates": [4.835, 45.758]},
     "population": 520000, "codeDepartement": "69"},
    {"nom": "Villeurbanne", "codesPostaux": ["69100"], "centre": {"coordinates": [4.88, 45.77]},
     "population": 150000, "codeDepartement": "69"},
    {"nom": "Paris", "codesPostaux": ["75001", "75015"], "centre": {"coordinates": [2.347, 48.859]},
     "population": 2100000, "codeDepartement": "75"},
    {"nom": "Laon", "codesPostaux": ["02000"], "centre": {"coordinates": [3.62, 49.564]},
     "population": 25000, "codeDepartement": "02"},
]


def test_locate_and_distance(tmp_path):
    geo = Geo(tmp_path / "communes.json")
    assert geo.load(download=lambda: COMMUNES)
    assert geo.locate("Lyon 69003") == (45.758, 4.835)
    assert geo.locate("Laon 02000 02000") == (49.564, 3.62)
    assert geo.locate("Paris, France") == (48.859, 2.347)
    assert geo.locate("Villeurbanne") == (45.77, 4.88)
    assert geo.locate("69***")  # eBay : département seulement
    assert geo.locate("Quelque part") is None
    home = geo.locate("75015")
    assert 380 < geo.distance(home, "Lyon 69003") < 400  # Paris → Lyon ≈ 390 km
    assert round(distance_km((0, 0), (0, 1))) == 111


def test_download_happens_once_and_errors_are_soft(tmp_path):
    geo = Geo(tmp_path / "communes.json")
    calls = []
    geo.load(download=lambda: calls.append(1) or COMMUNES)
    Geo(tmp_path / "communes.json").load(download=lambda: calls.append(1) or COMMUNES)
    assert calls == [1]  # le fichier local est réutilisé

    def broken():
        raise OSError("pas de réseau")

    offline = Geo(tmp_path / "absent" / "communes.json")
    assert not offline.load(download=broken) and "réseau" in offline.error
    assert offline.distance((45, 4), "Lyon 69003") is None
