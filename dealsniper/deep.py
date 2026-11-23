"""Analyse de marché approfondie d'UNE annonce, à la demande (bouton de la fiche détaillée).

À la différence de la notation de masse (Haiku, rapide et peu chère), on emploie ici un modèle plus
puissant AVEC recherche web, pour aller chercher les prix de vente RÉELS et récents du modèle exact
et donner une fourchette de revente fiable. Coûte quelques centimes : uniquement sur demande, pour
les annonces qui vous intéressent vraiment.
"""

from __future__ import annotations

import base64
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
De même pour une pièce détachée vendue seule (« Ventilateur ThinkPad T480 ») : prix de cette pièce,
pas celui de l'appareil.

Donne une fourchette prudente (bas / typique / haut), une confiance (« faible » / « moyenne » / « élevée »)
selon le nombre de prix réels trouvés, et 2 à 4 sources courtes au format « site : fourchette » (ex.
« eBay vendus : 180-210 € »).

Donne aussi « ventes » : jusqu'à 12 ventes ou annonces RÉELLEMENT observées pendant ta recherche, chacune avec
son mois (date) et son prix. N'invente aucun point : si tu n'as pas trouvé de prix daté, renvoie une liste vide.

Et « message_negociation » : un message court (2-3 phrases), poli, naturel et prêt à copier-coller pour le
vendeur (leboncoin/Vinted/Facebook), proposant un prix d'environ 90 % de ta revente typique, moins le coût de
la réparation éventuelle, moins la marge visée : 30 % de la revente typique, au moins 10 € (ce qui laisse une
marge nette à l'acheteur).

Si une PHOTO de l'annonce est jointe, regarde-la attentivement : état visible (rayures, chocs, écran fissuré,
oxydation, usure, pièces manquantes), panne visible, cohérence avec le titre (bon modèle ? photo de catalogue
ou trouvée sur internet plutôt que l'objet réel ?). Tiens-en compte dans la fourchette et les pièges, et donne
« photo » : {"etat": "bon|moyen|mauvais|inconnu", "constat": "1 à 2 phrases sur ce que montre la photo",
"alertes": ["défaut ou doute visible", "..."]}. Photo floue, trop petite ou sans l'objet : "etat": "inconnu".
N'invente rien que la photo ne montre pas.

Termine TOUJOURS par un unique bloc JSON, sans rien après :
{"revente_basse": n, "revente_typique": n, "revente_haute": n, "confiance": "faible|moyenne|élevée",
 "sources": ["...", "..."], "resume": "une phrase", "pieges": "pièges éventuels en 8 mots max",
 "ventes": [{"date": "AAAA-MM", "prix": n, "source": "ebay|leboncoin|vinted|backmarket"}],
 "message_negociation": "...",
 "photo": {"etat": "bon|moyen|mauvais|inconnu", "constat": "...", "alertes": ["..."]}}"""

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


PHOTO_MAX_BYTES = 4_500_000  # l'API accepte 5 Mo par image
_IMAGE_TYPES = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG", "image/png"), (b"GIF8", "image/gif"))


def _image_type(data: bytes) -> str | None:
    """Type réel d'une image d'après ses premiers octets (l'en-tête HTTP ment parfois)."""
    for magic, kind in _IMAGE_TYPES:
        if data.startswith(magic):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def photo_block(url: str | None, fetch: Any = None) -> dict[str, Any] | None:
    """Photo de l'annonce prête à envoyer à l'IA (bloc image), ou None si absente / illisible / trop lourde.
    Téléchargée ici (une requête) plutôt que par l'API : certains sites refusent les robots d'Anthropic."""
    if not url or not str(url).startswith(("http://", "https://")):
        return None
    try:
        if fetch is None:
            import httpx

            resp = httpx.get(url, timeout=10, follow_redirects=True,
                             headers={"User-Agent": "DealSniper/3.0 (analyse de la photo, usage personnel)"})
            if resp.status_code != 200:
                return None
            data = resp.content
        else:
            data = fetch(url)
    except Exception as exc:  # réseau, délai… : l'analyse se fait sans la photo
        log.info("Photo non récupérée (%s) : analyse sans photo", exc)
        return None
    kind = _image_type(data or b"")
    if not kind or len(data) > PHOTO_MAX_BYTES:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": kind,
                                       "data": base64.standard_b64encode(data).decode("ascii")}}


PHOTO_STATES = ("bon", "moyen", "mauvais", "inconnu")


def _photo(raw: Any) -> dict[str, Any] | None:
    """Constat de l'IA sur la photo, nettoyé (None si elle n'en a pas donné)."""
    if not isinstance(raw, dict):
        return None
    state = str(raw.get("etat", "")).strip().lower()
    alerts = [str(a).strip()[:140] for a in (raw.get("alertes") or []) if str(a).strip()][:4]
    return {"etat": state if state in PHOTO_STATES else "inconnu",
            "constat": str(raw.get("constat") or "").strip()[:320], "alertes": alerts}


def _clamp(value: Any) -> float | None:
    try:
        return round(max(0.0, float(value)), 2)
    except (TypeError, ValueError):
        return None


def clip(text: str, limit: int) -> str:
    """Texte raccourci sur un mot entier, avec « … » (jamais coupé au milieu d'un mot : « la cons »)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-–") + "…"


def build_negotiation_message(ad: Ad, max_buy: float | None = None) -> str:
    """Message de négociation poli, prêt à copier-coller, basé sur le prix conseillé."""
    title = clip(ad.title, 80)
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
        "marge_visee": target_profit(typical),
        "message_negociation": message or build_negotiation_message(ad, limit),  # repli sans IA
        "photo": _photo(raw.get("photo")),
    }


TARGET_SHARE = 0.30  # bénéfice net visé : 30 % de la revente…
TARGET_MIN = 10      # … et au moins 10 € (choix de Marius : un objet revendu 25 € peut encore valoir le coup)


def target_profit(typical: float | None) -> float:
    """Bénéfice net visé (€) : proportionnel à la revente (un fixe de +30 € rendait les petits objets
    impossibles à acheter : achat max négatif pour une revente de 25 €)."""
    return round(max(TARGET_MIN, TARGET_SHARE * float(typical or 0)))


def max_buy(typical: float | None, piece: float = 0.0) -> float | None:
    """Prix d'achat à ne pas dépasser pour viser la marge : revente TYPIQUE − 10 % (frais, négociation)
    − pièce − marge visée (30 % de la revente, au moins 10 €).
    (La revente basse servait de base avant : avec les 10 %, la prudence était comptée deux fois.)"""
    return round(typical * 0.9 - piece - target_profit(typical)) if typical else None


def apply_to_card(analysis: dict[str, Any] | None, deep: dict[str, Any] | None, price: float | None,
                  auction: bool = False) -> dict[str, Any] | None:
    """La carte reprend l'analyse approfondie (prix réels vérifiés, photo examinée) : revente typique,
    marge recalculée (revente − prix − pièce − 10 % de frais) et note alignée sur le verdict (à acheter :
    7 au moins, sauf confiance faible ; à négocier : 6 au plus ; à laisser passer : 3 au plus).
    La note rapide est gardée à côté."""
    typical = (deep or {}).get("revente_typique") or (deep or {}).get("revente_basse")
    if not analysis or not typical:
        return analysis
    if analysis.get("source_revente") == "analyse approfondie":
        analysis = {**analysis, "score_rentabilite": analysis.get("score_rapide", analysis["score_rentabilite"])}
    piece = float(analysis.get("cout_piece_estime") or 0)
    limit = max_buy(typical, piece)
    quick = int(analysis.get("score_rentabilite") or 0)
    if limit is None or limit <= 0:
        score = min(quick, 3)
    elif auction or price is None or price <= limit:
        # Confiance faible (peu de prix réels, annonce douteuse) : l'analyse peut baisser la note, pas la monter
        score = quick if deep.get("confiance") == "faible" else max(quick, 7)
    elif price <= limit * 1.25:
        score = min(max(quick, 5), 6)
    else:
        score = min(quick, 3)
    margin = analysis.get("marge_nette_estimee")
    if price is not None and not auction:
        margin = round(typical * 0.9 - price - piece, 2)
    return {**analysis, "valeur_revente_estimee": typical, "marge_nette_estimee": margin,
            "score_rentabilite": score, "score_rapide": quick, "source_revente": "analyse approfondie"}


def refresh(deep: dict[str, Any], piece: float = 0.0) -> dict[str, Any]:
    """Recalcule le prix d'achat max d'une analyse déjà enregistrée (formule à jour)."""
    if not deep:
        return deep
    typical = deep.get("revente_typique") or deep.get("revente_basse")
    return {**deep, "prix_achat_max": max_buy(typical, piece), "marge_visee": target_profit(typical)}


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


def analyze_market(ad: Ad, config: dict[str, Any], client: Any = None, fetch: Any = None) -> dict[str, Any]:
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

    photo = photo_block(ad.image, fetch)
    content: Any = [photo, {"type": "text", "text": _describe(ad) + "\nPhoto de l'annonce jointe."}] if photo \
        else _describe(ad) + "\nAucune photo disponible."
    messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
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
    if not photo:
        result["photo"] = None  # aucune photo envoyée : pas de constat (même si le modèle en a inventé un)
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
    questions = [clip(str(q), 240) for q in (items or []) if str(q).strip()][:5]
    if not questions:
        raise AnalysisFailed("Aucune question proposée, réessayez.", cost)
    return {"questions": questions, "cost_usd": round(cost, 5)}


# ---------- Analyse d'un lien collé (onglet « Analyse ») ----------

LINK_MODEL = "claude-opus-5-5"  # le meilleur modèle courant : une annonce à la fois, à la demande
PHOTO_JSON = '"photo": {"etat": "bon|moyen|mauvais|inconnu", "constat": "...", "alertes": ["..."]}'
LINK_PROMPT = SYSTEM_PROMPT.replace("Termine TOUJOURS par un unique bloc JSON", """Ici l'acheteur te donne UNE annonce \
qu'il hésite à acheter : fais un BILAN complet et franc, comme un ami expert. Plusieurs photos peuvent être jointes :
examine-les toutes. Ajoute au JSON :
- "modele_exact" : le modèle identifié (génération, configuration), ou ce qui reste incertain ;
- "note" : note globale de l'affaire sur 10 (prix, état, risque, facilité de revente ou d'usage) ;
- "points_forts" et "points_faibles" : 2 à 4 éléments courts chacun ;
- "arnaque" : {"niveau": "faible|moyen|élevé", "raisons": ["..."]} (prix trop beau, photo de catalogue,
  vendeur pressé, paiement hors site, description incohérente…) ;
- "a_verifier" : 3 à 6 points à contrôler à la remise en main propre ou à la réception ;
- "questions" : 3 ou 4 questions précises à poser au vendeur avant d'acheter.
Termine TOUJOURS par un unique bloc JSON""").replace(PHOTO_JSON + "}",
    PHOTO_JSON + ",\n"
    ' "modele_exact": "...", "note": n, "points_forts": ["..."], "points_faibles": ["..."],\n'
    ' "arnaque": {"niveau": "faible|moyen|élevé", "raisons": ["..."]}, "a_verifier": ["..."], "questions": ["..."]}')
LINK_SEARCH_TOOL = {**WEB_SEARCH_TOOL, "max_uses": 8}
SCAM_LEVELS = ("faible", "moyen", "élevé")


def _texts(raw: Any, limit: int, size: int = 160) -> list[str]:
    return [str(x).strip()[:size] for x in (raw if isinstance(raw, list) else []) if str(x).strip()][:limit]


def analyze_link(ad: Ad, photos: list[str], config: dict[str, Any], client: Any = None,
                 fetch: Any = None) -> dict[str, Any]:
    """Bilan complet d'une annonce collée : Claude Opus 5.5 (réflexion poussée) + recherche web + photos.
    Renvoie la même base que l'analyse approfondie (revente, achat max, ventes, photo…) plus le bilan."""
    if not env("ANTHROPIC_API_KEY"):
        raise RuntimeError("Clé Anthropic manquante (ANTHROPIC_API_KEY dans .env).")
    cfg = config["analyzer"]
    model = cfg.get("link_model") or LINK_MODEL
    pricing = {**DEFAULT_PRICING, **{k: tuple(v) for k, v in (cfg.get("pricing") or {}).items()}}

    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), max_retries=2,
                                     timeout=max(240.0, float(cfg.get("timeout_seconds", 90))))

    blocks = [b for b in (photo_block(url, fetch) for url in photos[:4]) if b]
    note = f"\n{len(blocks)} photo(s) de l'annonce jointe(s)." if blocks else "\nAucune photo disponible."
    place = f"\nVille : {ad.location}" if ad.location else ""
    text = f"Site : {ad.source}\nLien : {ad.url}\n{_describe_full(ad)}{place}{note}"
    messages: list[dict[str, Any]] = [{"role": "user", "content": [*blocks, {"type": "text", "text": text}]}]
    cost, out = 0.0, ""
    resp = None
    for _ in range(6):  # recherche web : plusieurs tours possibles (pause_turn)
        # Refus du modèle : l'API relance d'elle-même sur un modèle de repli adapté (« fallbacks »).
        resp = client.beta.messages.create(
            model=model, max_tokens=16000, system=LINK_PROMPT, messages=messages, tools=[LINK_SEARCH_TOOL],
            output_config={"effort": cfg.get("link_effort") or "high"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        served = getattr(resp, "model", model) or model
        price_in, price_out = pricing.get(served, pricing.get(model, (4.0, 20.0)))
        cost += _usage_cost(getattr(resp, "usage", None), price_in, price_out,
                            float(cfg.get("web_search_usd", WEB_SEARCH_USD)))
        out = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if getattr(resp, "stop_reason", "") == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    if getattr(resp, "stop_reason", "") == "refusal":
        raise AnalysisFailed("Le modèle a refusé cette analyse.", cost)
    try:
        parsed = _parse_json(out) if out else {}
    except ValueError as exc:
        raise AnalysisFailed("Réponse illisible, réessayez.", cost) from exc
    raw: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
    result = sanitize(raw, ad)
    if result["revente_typique"] is None:
        raise AnalysisFailed("Aucun prix de revente trouvé pour ce modèle, réessayez.", cost)
    if not blocks:
        result["photo"] = None
    scam: dict[str, Any] = raw["arnaque"] if isinstance(raw.get("arnaque"), dict) else {}
    level = str(scam.get("niveau", "")).strip().lower().replace("eleve", "élevé")
    note_value = _clamp(raw.get("note"))
    result.update(
        modele_exact=str(raw.get("modele_exact") or "").strip()[:160],
        note=None if note_value is None else min(10.0, round(note_value, 1)),
        points_forts=_texts(raw.get("points_forts"), 4), points_faibles=_texts(raw.get("points_faibles"), 4),
        arnaque={"niveau": level if level in SCAM_LEVELS else "moyen", "raisons": _texts(scam.get("raisons"), 4)},
        a_verifier=_texts(raw.get("a_verifier"), 6), questions=_texts(raw.get("questions"), 4, 220),
        photos_analysees=len(blocks), cost_usd=round(cost, 4),
        model=getattr(resp, "model", model) or model)
    return result


def _describe_full(ad: Ad) -> str:
    """Comme _describe, avec la description entière (une seule annonce : pas besoin de l'abréger)."""
    parts = [f"Titre : {ad.title}"]
    if ad.price is not None:
        parts.append(f"Prix demandé : {ad.price:.0f} €")
    if not ad.available:
        parts.append("Le site l'indique vendue / indisponible.")
    if ad.description:
        parts.append(f"Description du vendeur : {ad.description[:3000]}")
    return "\n".join(parts)
