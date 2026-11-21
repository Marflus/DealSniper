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

SYSTEM_PROMPT = """Tu es un expert de l'achat-revente d'occasion en France (électronique, sport, outillage…).
Pour l'annonce donnée, trouve le PRIX DE REVENTE RÉEL du modèle EXACT (même génération, même configuration)
sur le marché français de l'occasion. Utilise la recherche web : annonces en cours et surtout ventes récentes
sur leboncoin, eBay.fr (objets vendus), Vinted, Backmarket. Raisonne sur un appareil EN ÉTAT DE MARCHE une fois
réparé, jamais sur le prix neuf. Si l'annonce vend un JEU ou un ACCESSOIRE qui cite une console
(« Zelda … Nintendo Switch », « Manette PS5 »), cherche le prix de ce jeu / accessoire, pas celui de la console.

Donne une fourchette prudente (bas / typique / haut), une confiance (« faible » / « moyenne » / « élevée »)
selon le nombre de prix réels trouvés, et 2 à 4 sources courtes au format « site : fourchette » (ex.
« eBay vendus : 180-210 € »).

Donne aussi « ventes » : jusqu'à 12 ventes ou annonces RÉELLEMENT observées pendant ta recherche, chacune avec
son mois (date) et son prix. N'invente aucun point : si tu n'as pas trouvé de prix daté, renvoie une liste vide.

Et « message_negociation » : un message court (2-3 phrases), poli, naturel et prêt à copier-coller pour le
vendeur (leboncoin/Vinted/Facebook), proposant un prix d'environ 90 % de ta revente typique, moins le coût de
la réparation éventuelle, moins 30 € (ce qui laisse une marge nette à l'acheteur).

Termine TOUJOURS par un unique bloc JSON, sans rien après :
{"revente_basse": n, "revente_typique": n, "revente_haute": n, "confiance": "faible|moyenne|élevée",
 "sources": ["...", "..."], "resume": "une phrase", "pieges": "pièges éventuels en 8 mots max",
 "ventes": [{"date": "AAAA-MM", "prix": n, "source": "ebay|leboncoin|vinted|backmarket"}],
 "message_negociation": "..."}"""

WEB_SEARCH_USD = 0.01  # recherche web : 10 $ pour 1 000 recherches, facturée en plus des tokens
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5,
                   "user_location": {"type": "approximate", "country": "FR"}}


class AnalysisFailed(RuntimeError):
    """Analyse impossible (refus du modèle, réponse vide ou illisible). Porte le coût déjà dépensé,
    pour qu'il soit compté dans le budget du jour même sans résultat."""

    def __init__(self, message: str, cost_usd: float):
        super().__init__(message)
        self.cost_usd = round(cost_usd, 5)


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
        target = round(max_buy)
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
    sources = [str(s)[:80] for s in (raw.get("sources") or []) if str(s).strip()][:4]
    limit = max_buy(typical, piece)
    message = str(raw.get("message_negociation") or "").strip()[:500]
    return {
        "revente_basse": low, "revente_typique": typical, "revente_haute": high,
        "confiance": confiance, "sources": sources, "ventes": _sales(raw.get("ventes")),
        "resume": str(raw.get("resume", ""))[:300], "pieges": str(raw.get("pieges", ""))[:120],
        "prix_achat_max": limit,
        "message_negociation": message or build_negotiation_message(ad, limit),  # repli sans IA
    }


TARGET_PROFIT = 30  # bénéfice net visé (€) pour le prix d'achat maximum conseillé


def max_buy(typical: float | None, piece: float = 0.0) -> float | None:
    """Prix d'achat à ne pas dépasser pour viser +30 € net :
    revente TYPIQUE − 10 % (frais, négociation) − pièce − 30 €.
    (La revente basse servait de base avant : avec les 10 %, la prudence était comptée deux fois.)"""
    return round(typical * 0.9 - piece - TARGET_PROFIT) if typical else None


def refresh(deep: dict[str, Any], piece: float = 0.0) -> dict[str, Any]:
    """Recalcule le prix d'achat max d'une analyse déjà enregistrée (formule à jour)."""
    if not deep:
        return deep
    return {**deep, "prix_achat_max": max_buy(deep.get("revente_typique") or deep.get("revente_basse"), piece)}


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


def _usage_cost(usage: Any, price_in: float, price_out: float, search_usd: float) -> float:
    """Coût d'une réponse : tokens + recherches web (facturées à part, absentes des tokens)."""
    if usage is None:
        return 0.0
    tokens = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1e6
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
    return tokens + searches * search_usd


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
        cost += _usage_cost(getattr(resp, "usage", None), price_in, price_out,
                            float(cfg.get("web_search_usd", WEB_SEARCH_USD)))
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if getattr(resp, "stop_reason", "") == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break

    if getattr(resp, "stop_reason", "") == "refusal":
        raise AnalysisFailed("Le modèle a refusé cette analyse.", cost)
    try:
        raw = _parse_json(text) if text else {}
    except ValueError as exc:  # JSONDecodeError compris
        raise AnalysisFailed("Réponse illisible, réessayez.", cost) from exc
    result = sanitize(raw if isinstance(raw, dict) else {}, ad)
    if result["revente_typique"] is None:  # rien d'exploitable : on ne l'enregistre pas comme une analyse
        raise AnalysisFailed("Aucun prix de revente trouvé pour ce modèle, réessayez.", cost)
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
    cost = _usage_cost(getattr(resp, "usage", None), price_in, price_out, 0.0)
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    try:
        raw = _parse_json(text) if text else {}
    except ValueError as exc:
        raise AnalysisFailed("Réponse illisible, réessayez.", cost) from exc
    items = raw.get("questions") if isinstance(raw, dict) else None
    questions = [str(q).strip()[:160] for q in (items or []) if str(q).strip()][:5]
    if not questions:
        raise AnalysisFailed("Aucune question proposée, réessayez.", cost)
    return {"questions": questions, "cost_usd": round(cost, 5)}
