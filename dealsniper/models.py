"""Modèle de données : une annonce normalisée, quelle que soit sa source."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any

from .urls import ad_uid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Ad:
    title: str
    url: str
    source: str                         # "leboncoin", "ebay", nom du flux RSS…
    price: float | None = None          # prix affiché (€), None si inconnu
    shipping: float | None = None       # frais de livraison (€), None si non précisés
    description: str = ""               # texte court, sans HTML
    location: str | None = None         # « Lyon 69003 » si connu
    price_mode: str = "fixe"            # "fixe" ou "enchere" (prix provisoire)
    url_is_stable: bool = True          # False si le lien est un lien de suivi opaque
    seller: dict[str, Any] | None = None  # réputation du vendeur si la source la fournit (eBay)
    category: str | None = None         # renseigné par filters.py
    signals: list[str] = field(default_factory=list)  # mots-clés positifs (filters.py)
    collected_at: str = field(default_factory=_now_iso)
    analysis: dict[str, Any] | None = None             # renseigné par analyzer.py

    @property
    def total_price(self) -> float | None:
        """Prix + livraison (livraison inconnue comptée 0)."""
        if self.price is None:
            return None
        return round(self.price + (self.shipping or 0.0), 2)

    @property
    def uid(self) -> str:
        return ad_uid(self.url, self.title, self.price, self.url_is_stable)

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
        return " · ".join(parts)

    @property
    def delivery_possible(self) -> bool:
        """Vrai si l'annonce indique un envoi possible (« Livraison possible » sur leboncoin)."""
        text = self.description.lower()
        return self.shipping is not None or "livraison possible" in text or "envoi possible" in text

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ad:
        """Reconstruit une annonce depuis l'historique JSON (champs inconnus ignorés)."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(uid=self.uid, total_price=self.total_price)
        return data
