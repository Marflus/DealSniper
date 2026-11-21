"""Modèle de données : une annonce normalisée, quelle que soit sa source."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

from .utils.urls import ad_uid

SHIPPING_SITES = {"vinted"}  # envoi toujours proposé, même sans frais de port indiqués


@dataclass
class Ad:
    title: str
    url: str
    source: str                         # nom du site : "ebay", …
    price: float | None = None          # prix affiché (€), None si inconnu
    shipping: float | None = None       # frais de livraison (€), None si non précisés
    description: str = ""               # texte court, sans HTML
    location: str | None = None         # « Lyon 69003 » si connu
    price_mode: str = "fixe"            # "fixe" ou "enchere" (prix provisoire)
    image: str | None = None            # URL de la photo principale
    available: bool = True              # False si le site l'indique vendue, réservée ou en cours d'achat
    seller: dict[str, Any] | None = None  # réputation du vendeur si la source la fournit (eBay)
    category: str | None = None         # renseigné par filters.py
    signals: list[str] = field(default_factory=list)  # mots-clés positifs (filters.py)
    market: dict[str, Any] | None = None               # cote du modèle (market.py) : médiane, nombre, source
    analysis: dict[str, Any] | None = None             # renseigné par analyzer.py
    watched: bool = False                              # correspond à une veille (watch.py) : notifiée d'office
    ends_at: str | None = None                         # fin d'enchère (ISO) pour le rappel

    @property
    def shippable(self) -> bool:
        """Envoi possible : frais de port connus, ou site où l'envoi est la règle (Vinted ne donne pas le prix
        du port dans la liste, mais toutes ses annonces peuvent être envoyées)."""
        return self.shipping is not None or self.source in SHIPPING_SITES

    @property
    def total_price(self) -> float | None:
        """Prix + livraison (livraison inconnue comptée 0)."""
        if self.price is None:
            return None
        return round(self.price + (self.shipping or 0.0), 2)

    @property
    def uid(self) -> str:
        return ad_uid(self.url)

    @property
    def score(self) -> int:
        return int((self.analysis or {}).get("score_rentabilite", 0))

    @property
    def deal_type(self) -> str:
        return (self.analysis or {}).get("type_affaire", "reparation")

    @property
    def seller_summary(self) -> str:
        """« 99,2 % positifs · 1 534 avis » (vide si inconnu)."""
        if not self.seller:
            return ""
        parts = []
        if self.seller.get("positive_pct") is not None:
            parts.append(f"{float(self.seller['positive_pct']):.1f} % positifs".replace(".", ","))
        if self.seller.get("reviews") is not None:
            parts.append(f"{int(self.seller['reviews']):,} avis".replace(",", " "))
        if self.seller.get("type"):
            parts.append(str(self.seller["type"]))  # particulier / pro
        if self.seller.get("since"):
            parts.append(f"membre depuis {str(self.seller['since'])[:7]}")
        return " · ".join(parts)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ad:
        """Reconstruit une annonce depuis l'historique JSON (champs inconnus ignorés)."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(uid=self.uid, total_price=self.total_price, shippable=self.shippable)
        return data
