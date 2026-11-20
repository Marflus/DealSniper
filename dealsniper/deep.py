"""Analyse de marché approfondie d'UNE annonce, à la demande (bouton de la fiche détaillée).

À la différence de la notation de masse (Haiku, rapide et peu chère), on emploie ici un modèle plus
puissant AVEC recherche web, pour aller chercher les prix de vente RÉELS et récents du modèle exact
et donner une fourchette de revente fiable. Coûte quelques centimes : uniquement sur demande, pour
les annonces qui vous intéressent vraiment.
"""

from __future__ import annotations

import logging
from typing import Any

from .analyzer import DEFAULT_PRICING, _parse_json
from .config import env
from .models import Ad

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu es un expert de l'achat-revente d'électronique d'occasion en France.
Pour l'annonce donnée, trouve le PRIX DE REVENTE RÉEL du modèle EXACT (même génération, même configuration)
sur le marché français de l'occasion. Utilise la recherche web : annonces en cours et surtout ventes récentes
sur leboncoin, eBay.fr (objets vendus), Vinted, Backmarket. Raisonne sur un appareil EN ÉTAT DE MARCHE une fois
réparé, jamais sur le prix neuf.

Donne une fourchette prudente (bas / typique / haut), une confiance (« faible » / « moyenne » / « élevée »)
selon le nombre de prix réels trouvés, et 2 à 4 sources courtes au format « site : fourchette » (ex.
« eBay vendus : 180-210 € »).

Donne aussi « ventes » : jusqu'à 12 ventes ou annonces RÉELLEMENT observées pendant ta recherche, chacune avec
son mois (date) et son prix. N'invente aucun point : si tu n'as pas trouvé de prix daté, renvoie une liste vide.

Donne enfin « questions » : 3 à 5 questions courtes à poser au vendeur AVANT d'acheter, propres à CE modèle et
à SA panne (points faibles connus du modèle, verrouillages, pièces manquantes, état de la batterie…).

Et « message_negociation » : un message court (2-3 phrases), poli, naturel et prêt à copier-coller pour le
vendeur (leboncoin/Vinted/Facebook), proposant un prix cohérent avec la cote basse et l'éventuelle réparation.

Termine TOUJOURS par un unique bloc JSON, sans rien après :
{"revente_basse": n, "revente_typique": n, "revente_haute": n, "confiance": "faible|moyenne|élevée",
 "sources": ["...", "..."], "resume": "une phrase", "pieges": "pièges éventuels en 8 mots max",
 "ventes": [{"date": "AAAA-MM", "prix": n, "source": "ebay|leboncoin|vinted|backmarket"}],
 "questions": ["...", "..."],
 "message_negociation": "..."}"""

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5,
                   "user_location": {"type": "approximate", "country": "FR"}}


def _describe(ad: Ad) -> str:
    parts = [f"Titre : {ad.title}", f"Catégorie : {ad.category or '?'}"]
    if ad.price is not None:
        parts.append(f"Prix demandé : {ad.price:.0f} €")
    analysis = ad.analysis or {}
    if analysis.get("panne_probable") and analysis["panne_probable"].lower() != "aucune":
        parts.append(f"Panne : {analysis['panne_probable']}")
    if ad.market:
        parts.append(f"Cote interne (indicative) : {ad.market['median']:.0f} €")
    if ad.description:
        parts.append(f"Description : {ad.description[:400]}")
    return "\n".join(parts)


def _clamp(value: Any) -> float | None:
    try:
        return round(max(0.0, float(value)), 2)
    except (TypeError, ValueError):
        return None


def build_negotiation_message(ad: Ad, max_buy: float | None = None) -> str:
    """Message de négociation poli, prêt à copier-coller, basé sur le prix conseillé."""
    title = ad.title[:80].strip()
    panne = str((ad.analysis or {}).get("panne_probable") or "").strip()
    has_issue = bool(panne and panne.lower() != "aucune")
    asked = ad.price

    if max_buy is not None and max_buy > 0 and (asked is None or asked > max_buy):
        target = int(round(max_buy))
        if has_issue:
            return (
                f"Bonjour, votre annonce « {title} » est-elle toujours disponible ? "
                f"Compte tenu de la réparation à prévoir ({panne.lower()}) et du coût des pièces, "
                f"seriez-vous ouvert(e) à une offre à {target} € ? Je peux conclure rapidement. Merci !"
            )
        return (
            f"Bonjour, votre annonce « {title} » est-elle toujours disponible ? "
            f"Au vu des prix constatés sur ce modèle, seriez-vous d'accord pour {target} € ? "
            f"Je peux procéder à l'achat rapidement. Merci d'avance !"
        )

    return (
        f"Bonjour, votre annonce « {title} » est-elle toujours disponible ? "
        f"Pouvez-vous me confirmer son état exact ainsi que la possibilité d'un envoi ou d'une remise "
        f"en main propre rapide ? Merci d'avance !"
    )


def sanitize(raw: dict[str, Any], ad: Ad) -> dict[str, Any]:
    """Normalise la réponse et calcule un prix d'achat maximum conseillé (en Python)."""
    low = _clamp(raw.get("revente_basse"))
    typical = _clamp(raw.get("revente_typique")) or low
    high = _clamp(raw.get("revente_haute")) or typical
    confiance = str(raw.get("confiance", "")).strip().lower()
    if confiance not in ("faible", "moyenne", "élevée"):
        confiance = "moyenne"
    piece = float((ad.analysis or {}).get("cout_piece_estime") or 0)
    # Prix d'achat à ne pas dépasser pour viser +30 € net (revente basse − pièce − 10 % de frais − 30).
    max_buy = round(low * 0.9 - piece - 30) if low else None
    sources = [str(s)[:80] for s in (raw.get("sources") or []) if str(s).strip()][:4]
    raw_msg = str(raw.get("message_negociation") or "").strip()[:500]
    return {
        "revente_basse": low, "revente_typique": typical, "revente_haute": high,
        "confiance": confiance, "sources": sources, "ventes": _sales(raw.get("ventes")),
        "resume": str(raw.get("resume", ""))[:300], "pieges": str(raw.get("pieges", ""))[:120],
        "prix_achat_max": max_buy,
        "questions": [str(q).strip()[:160] for q in (raw.get("questions") or []) if str(q).strip()][:5],
        "message_negociation": raw_msg or build_negotiation_message(ad, max_buy),
    }


def _sales(rows: Any) -> list[dict[str, Any]]:
    """Ventes observées (date AAAA-MM et prix), nettoyées pour le graphique."""
    import re

    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        date = re.match(r"(\d{4})-(\d{2})(?:-(\d{2}))?", str(row.get("date", "")))
        price = _clamp(row.get("prix"))
        if date and price:
            out.append({"date": date.group(0), "prix": price,
                        "source": str(row.get("source", ""))[:20].lower()})
    return sorted(out, key=lambda v: v["date"])[:20]


def analyze_market(ad: Ad, config: dict[str, Any], client: Any = None) -> dict[str, Any]:
    """Interroge un gros modèle + recherche web ; renvoie la fourchette, les sources et le coût."""
    if not env("ANTHROPIC_API_KEY"):
        raise RuntimeError("Clé Anthropic manquante (ANTHROPIC_API_KEY dans .env).")
    cfg = config["analyzer"]
    model = cfg.get("deep_model") or "claude-sonnet-5-5"
    pricing = {**DEFAULT_PRICING, **{k: tuple(v) for k, v in (cfg.get("pricing") or {}).items()}}
    price_in, price_out = pricing.get(model, (2.0, 10.0))

    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), max_retries=2,
                                     timeout=float(cfg.get("timeout_seconds", 90)))

    messages: list[dict[str, Any]] = [{"role": "user", "content": _describe(ad)}]
    cost = 0.0
    text = ""
    for _ in range(5):  # la recherche web peut demander plusieurs tours (pause_turn)
        resp = client.messages.create(model=model, max_tokens=2000, system=SYSTEM_PROMPT,
                                      messages=messages, tools=[WEB_SEARCH_TOOL],
                                      output_config={"effort": "low"})
        usage = getattr(resp, "usage", None)
        if usage:
            cost += (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1e6
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if getattr(resp, "stop_reason", "") == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break

    result = sanitize(_parse_json(text) if text else {}, ad)
    result["cost_usd"] = round(cost, 4)
    result["model"] = model
    return result