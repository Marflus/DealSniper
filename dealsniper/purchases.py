"""Suivi des achats : bénéfices réalisés et calibrage automatique de la prudence des estimations.

Quand au moins 3 achats ont été revendus, on compare le prix de revente réel à la revente estimée
au moment de l'achat. Si vos ventes se font en moyenne à 85 % de l'estimation, le rabot
`resale_haircut` est multiplié par 0,85 : les estimations suivantes collent à vos vrais résultats.
"""

from __future__ import annotations

import copy
import logging
import statistics
from typing import Any

log = logging.getLogger(__name__)

MIN_SALES = 3


def _cost(p: dict[str, Any]) -> float:
    return float(p.get("buy_price") or 0) + float(p.get("repair_cost") or 0)


def summary(purchases: list[dict[str, Any]]) -> dict[str, Any]:
    """Totaux de l'onglet Achats."""
    sold = [p for p in purchases if p.get("sold_price") is not None]
    stock = [p for p in purchases if p.get("sold_price") is None]
    cost_sold = sum(_cost(p) for p in sold)
    profit = sum(float(p["sold_price"]) - _cost(p) for p in sold)
    return {
        "count": len(purchases), "sold": len(sold), "in_stock": len(stock),
        "invested": round(sum(_cost(p) for p in purchases), 2),
        "revenue": round(sum(float(p["sold_price"]) for p in sold), 2),
        "profit": round(profit, 2),
        "stock_value": round(sum(_cost(p) for p in stock), 2),
        "roi_pct": round(100 * profit / cost_sold, 1) if cost_sold else None,
    }


def calibration(purchases: list[dict[str, Any]], base_haircut: float) -> dict[str, Any]:
    """Rapport ventes réelles / estimations, et rabot corrigé (None tant qu'il y a moins de 3 ventes)."""
    ratios = [float(p["sold_price"]) / float(p["estimated_resale"]) for p in purchases
              if p.get("sold_price") and p.get("estimated_resale")]
    if len(ratios) < MIN_SALES:
        return {"sales": len(ratios), "needed": MIN_SALES, "ratio": None, "haircut": None}
    ratio = statistics.median(ratios)
    return {"sales": len(ratios), "needed": MIN_SALES, "ratio": round(ratio, 2),
            "haircut": round(min(1.1, max(0.5, base_haircut * ratio)), 2)}


def calibrated_config(config: dict[str, Any], purchases: list[dict[str, Any]]) -> dict[str, Any]:
    """Configuration dont le rabot suit vos ventes réelles (si `analyzer.auto_calibrate` et assez de ventes)."""
    analyzer = config["analyzer"]
    if not analyzer.get("auto_calibrate", True):
        return config
    result = calibration(purchases, float(analyzer["resale_haircut"]))
    if result["haircut"] is None:
        return config
    log.info("Rabot calibré sur %d vente(s) réelle(s) : %.2f → %.2f", result["sales"],
             analyzer["resale_haircut"], result["haircut"])
    tuned = copy.deepcopy(config)
    tuned["analyzer"]["resale_haircut"] = result["haircut"]
    return tuned
