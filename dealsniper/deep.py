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

Termine TOUJOURS par un unique bloc JSON, sans rien après :
{"revente_basse": n, "revente_typique": n, "revente_haute": n, "confiance": "faible|moyenne|élevée",
 "sources": ["...", "..."], "resume": "une phrase", "pieges": "pièges éventuels en 8 mots max",
 "ventes": [{"date": "AAAA-MM", "prix": n, "source": "ebay|leboncoin|vinted|backmarket"}]}"""

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
    return {
        "revente_basse": low, "revente_typique": typical, "revente_haute": high,
        "confiance": confiance, "sources": sources, "ventes": _sales(raw.get("ventes")),
        "resume": str(raw.get("resume", ""))[:300], "pieges": str(raw.get("pieges", ""))[:120],
        "prix_achat_max": max_buy,
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


QUESTIONS_PROMPT = """Tu aides un acheteur d'électronique d'occasion en France. Donne 4 questions courtes à poser au
vendeur AVANT d'acheter cette annonce, propres à CE modèle et à SA panne (points faibles connus du modèle,
verrouillages, pièces manquantes, état de la batterie…). Réponds UNIQUEMENT par {"questions": ["...", "..."]}."""


def seller_questions(ad: Ad, config: dict[str, Any], deep: dict[str, Any] | None = None,
                     client: Any = None) -> dict[str, Any]:
    """Questions au vendeur sur mesure, à la demande : petit modèle, sans recherche web (≈ 0,001 $)."""
    if not env("ANTHROPIC_API_KEY"):
        raise RuntimeError("Clé Anthropic manquante (ANTHROPIC_API_KEY dans .env).")
    cfg = config["analyzer"]
    model = cfg.get("model") or "claude-haiku-5-5"
    pricing = {**DEFAULT_PRICING, **{k: tuple(v) for k, v in (cfg.get("pricing") or {}).items()}}
    price_in, price_out = pricing.get(model, (1.0, 5.0))
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), max_retries=2,
                                     timeout=float(cfg.get("timeout_seconds", 90)))
    deep = deep or {}
    context = _describe(ad) + (f"\nAnalyse : {deep.get('resume')} {deep.get('pieges') or ''}" if deep else "")
    params: dict[str, Any] = {"model": model, "max_tokens": 500, "system": QUESTIONS_PROMPT,
                              "messages": [{"role": "user", "content": context}]}
    if model.startswith("claude-haiku-5"):
        params["thinking"] = {"type": "disabled"}
    resp = client.messages.create(**params)
    usage = getattr(resp, "usage", None)
    cost = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1e6 if usage else 0.0
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    raw = _parse_json(text) if text else {}
    questions = [str(q).strip()[:160] for q in (raw.get("questions") or []) if str(q).strip()][:5]
    return {"questions": questions, "cost_usd": round(cost, 5)}
