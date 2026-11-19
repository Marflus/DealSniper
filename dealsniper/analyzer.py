"""Évaluation des annonces pré-filtrées par Claude (API Anthropic).

Coût minimal :
  * modèle économique (`claude-haiku-4-5` par défaut, réglable dans config.yaml) ;
  * annonces envoyées par lots (un seul prompt système pour N annonces) ;
  * une ligne compacte par annonce, description tronquée (600 caractères) ;
  * réponse contrainte par un schéma JSON (structured outputs) ;
  * coût calculé à partir de `response.usage` et plafond de dépense journalier.

Robustesse :
  * erreurs temporaires (429, 5xx, réseau) réessayées par le SDK (`max_retries`) ;
  * lot en échec → annonces renvoyées dans `failed`, retentées au passage suivant ;
  * réponse tronquée (`max_tokens`) → lot coupé en deux ;
  * clé refusée / crédit épuisé → arrêt propre, les lots déjà analysés sont gardés.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .config import env
from .models import Ad
from .text import truncate

log = logging.getLogger(__name__)

# Prix publics en $ par million de tokens (entrée, sortie) ; surchargeables via `analyzer.pricing`.
DEFAULT_PRICING = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}
DEAL_TYPES = ("reparation", "revente_directe")
CONFIDENCE_LEVELS = ("faible", "moyenne", "haute")

SYSTEM_PROMPT = """Tu es expert en achat-revente d'électronique d'occasion sur le marché français (leboncoin, eBay.fr, Back Market). Toutes les annonces sont situées en France.
Pour chaque annonce, estime la rentabilité d'un achat pour revente en France. Deux types d'affaires :
- "reparation" : objet en panne ou incomplet, à remettre en état puis revendre.
- "revente_directe" : objet fonctionnel vendu nettement sous sa cote d'occasion, revendable tel quel.

Barème score_rentabilite (entier 1-10) :
- 8-10 : marge nette ≥ 40 € ET risque faible. Réparation : panne facile et peu coûteuse (pièce < 40 €, < 1 h), ex. PC pro sans disque/SSD ou sans OS, batterie HS, chargeur absent, port HDMI/USB-C dessoudé, rétroéclairage TV/écran, console qui surchauffe, outillage vendu sans batterie, nappe/clavier/écran courant. Revente directe : fonctionnel, au moins 30 % sous la cote, modèle recherché, annonce crédible.
- 4-7 : réparation moyenne, pièce chère, décote modeste, ou marge faible/incertaine.
- 1-3 : panne lourde ou inconnue (carte mère, oxydation, liquide, verrouillage iCloud/BIOS/compte), annonce trop vague, prix proche de la cote, accessoire seul, ou prix anormalement bas sur un produit récent et recherché (arnaque probable).

Règles :
- Montants en euros. prix_total = prix + livraison. Un prix marqué "enchère" est provisoire : raisonne sur un prix final probable.
- valeur_revente_estimee : prix d'occasion réaliste en France (après réparation le cas échéant), jamais le prix neuf ni un prix demandé par d'autres vendeurs.
- marge_nette_estimee = valeur_revente_estimee - prix_total - cout_piece_estime - 10 % de frais de revente.
- Revente directe : panne_probable = "aucune", cout_piece_estime = 0 (sauf petit accessoire manquant).
- confiance : "haute" si modèle et état sont clairs, "faible" si l'annonce est vague. Dans le doute, baisse le score.
- question_vendeur : la question la plus utile à poser au vendeur avant d'acheter.
- panne_probable : 8 mots max. justification_courte : 20 mots max. question_vendeur : 15 mots max.
- Renvoie une entrée par annonce, avec son id."""

_ITEM_FIELDS: dict[str, Any] = {
    "id": {"type": "integer"},
    "type_affaire": {"type": "string", "enum": list(DEAL_TYPES)},
    "score_rentabilite": {"type": "integer"},
    "confiance": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
    "panne_probable": {"type": "string"},
    "cout_piece_estime": {"type": "number"},
    "valeur_revente_estimee": {"type": "number"},
    "marge_nette_estimee": {"type": "number"},
    "justification_courte": {"type": "string"},
    "question_vendeur": {"type": "string"},
}
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "annonces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _ITEM_FIELDS,
                "required": list(_ITEM_FIELDS),
                "additionalProperties": False,
            },
        }
    },
    "required": ["annonces"],
    "additionalProperties": False,
}


class FatalAPIError(RuntimeError):
    """Erreur non récupérable : clé invalide, accès refusé, crédit épuisé."""


@dataclass
class AnalysisResult:
    analyzed: list[Ad] = field(default_factory=list)
    failed: list[Ad] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    api_calls: int = 0


# --------------------------------------------------------------------------- #
#  Prompt
# --------------------------------------------------------------------------- #
def _money(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"


def format_ad(idx: int, ad: Ad, max_chars: int) -> str:
    """Une annonce = une ligne compacte : chaque caractère économisé est un token en moins."""
    price = f"{_money(ad.price)} €"
    if ad.shipping:
        price += f" + {_money(ad.shipping)} € port"
    if ad.price_mode == "enchere":
        price += " (enchère)"
    parts = [f"#{idx} [{ad.category or '?'}] {ad.title}", price]
    if ad.signals:
        parts.append("signaux: " + ", ".join(ad.signals))
    description = truncate(ad.description.replace(ad.title, "").strip(), max_chars)
    if description:
        parts.append(description)
    return " | ".join(parts)


def build_user_prompt(ads: list[Ad], max_chars: int) -> str:
    return "Annonces :\n" + "\n".join(format_ad(i, ad, max_chars) for i, ad in enumerate(ads, 1))


# --------------------------------------------------------------------------- #
#  Réponse
# --------------------------------------------------------------------------- #
def _parse_json(text: str) -> dict[str, Any]:
    """Parse la réponse ; tolère du texte autour du JSON (mode sans schéma)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def sanitize_analysis(raw: dict[str, Any]) -> dict[str, Any]:
    """Types, bornes et longueurs des valeurs renvoyées par le modèle."""

    def num(key: str) -> float:
        try:
            return round(float(raw.get(key) or 0), 2)
        except (TypeError, ValueError):
            return 0.0

    try:
        score = int(round(float(raw.get("score_rentabilite", 1))))
    except (TypeError, ValueError):
        score = 1
    deal_type = str(raw.get("type_affaire", "")).strip().lower()
    if deal_type not in DEAL_TYPES:
        deal_type = "revente_directe" if "direct" in deal_type else "reparation"
    confidence = str(raw.get("confiance", "")).strip().lower()
    return {
        "type_affaire": deal_type,
        "score_rentabilite": max(1, min(10, score)),
        "confiance": confidence if confidence in CONFIDENCE_LEVELS else "moyenne",
        "panne_probable": str(raw.get("panne_probable", ""))[:120],
        "cout_piece_estime": num("cout_piece_estime"),
        "valeur_revente_estimee": num("valeur_revente_estimee"),
        "marge_nette_estimee": num("marge_nette_estimee"),
        "justification_courte": str(raw.get("justification_courte", ""))[:240],
        "question_vendeur": str(raw.get("question_vendeur", ""))[:200],
    }


# --------------------------------------------------------------------------- #
#  Analyse
# --------------------------------------------------------------------------- #
class Analyzer:
    def __init__(self, config: dict[str, Any]):
        import anthropic

        self._anthropic = anthropic
        cfg = config["analyzer"]
        self.model: str = cfg["model"]
        self.batch_size = max(1, int(cfg["batch_size"]))
        self.max_chars = int(cfg["max_description_chars"])
        self.max_tokens = int(cfg["max_tokens"])
        self.effort: str | None = cfg.get("effort")
        self.use_schema = bool(cfg["structured_output"])
        pricing = {**DEFAULT_PRICING, **{k: tuple(v) for k, v in (cfg.get("pricing") or {}).items()}}
        self.price_in, self.price_out = pricing.get(self.model, (1.0, 5.0))
        self.client = anthropic.Anthropic(
            api_key=env("ANTHROPIC_API_KEY"),
            max_retries=int(cfg["max_retries"]),
            timeout=float(cfg["timeout_seconds"]),
        )

    def _request(self, ads: list[Ad], result: AnalysisResult) -> tuple[dict[str, Any], str]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": build_user_prompt(ads, self.max_chars)}],
        }
        output_config: dict[str, Any] = {}
        if self.use_schema:
            output_config["format"] = {"type": "json_schema", "schema": RESPONSE_SCHEMA}
        else:
            params["system"] += '\nRéponds UNIQUEMENT par un objet JSON {"annonces": [...]} sans texte autour.'
        if self.effort:
            output_config["effort"] = self.effort
        if output_config:
            params["output_config"] = output_config

        response = self.client.messages.create(**params)
        result.api_calls += 1
        result.input_tokens += response.usage.input_tokens
        result.output_tokens += response.usage.output_tokens
        result.cost_usd += (
            response.usage.input_tokens * self.price_in + response.usage.output_tokens * self.price_out
        ) / 1e6
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        return (_parse_json(text) if text else {}), (response.stop_reason or "")

    def _analyze_batch(self, ads: list[Ad], result: AnalysisResult) -> None:
        anthropic = self._anthropic
        try:
            data, stop_reason = self._request(ads, result)
        except anthropic.AuthenticationError as exc:
            raise FatalAPIError(f"clé API refusée : {exc}") from exc
        except anthropic.PermissionDeniedError as exc:
            raise FatalAPIError(f"accès refusé : {exc}") from exc
        except anthropic.BadRequestError as exc:
            message = str(exc).lower()
            if "credit" in message or "billing" in message:
                raise FatalAPIError(f"crédit API insuffisant : {exc}") from exc
            if self.use_schema and any(w in message for w in ("output_config", "format", "schema")):
                log.warning("Schéma JSON refusé par %s → mode JSON simple.", self.model)
                self.use_schema = False
                return self._analyze_batch(ads, result)
            log.error("Requête refusée (400), lot de %d reporté : %s", len(ads), exc)
            result.failed.extend(ads)
            return
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
            log.error("Erreur API temporaire (%s), lot de %d reporté.", type(exc).__name__, len(ads))
            result.failed.extend(ads)
            return
        except (json.JSONDecodeError, ValueError) as exc:
            log.error("Réponse JSON illisible (%s), lot de %d reporté.", exc, len(ads))
            result.failed.extend(ads)
            return

        if stop_reason == "max_tokens" and len(ads) > 1:
            log.warning("Réponse tronquée : lot de %d coupé en deux.", len(ads))
            middle = len(ads) // 2
            self._analyze_batch(ads[:middle], result)
            self._analyze_batch(ads[middle:], result)
            return
        if stop_reason == "refusal":
            log.warning("Le modèle a refusé ce lot de %d annonce(s).", len(ads))
            result.failed.extend(ads)
            return

        by_id: dict[int, dict[str, Any]] = {}
        for item in data.get("annonces", []) if isinstance(data, dict) else []:
            try:
                by_id[int(item.get("id"))] = item
            except (TypeError, ValueError):
                continue
        for idx, ad in enumerate(ads, 1):
            if idx in by_id:
                ad.analysis = sanitize_analysis(by_id[idx])
                result.analyzed.append(ad)
            else:
                log.warning("Pas de réponse pour « %s », reportée.", ad.title[:50])
                result.failed.append(ad)

    def analyze(self, ads: list[Ad], budget_left_usd: float | None = None) -> AnalysisResult:
        """Analyse lot par lot. Ne lève jamais : les erreurs finissent dans `failed`."""
        result = AnalysisResult()
        for start in range(0, len(ads), self.batch_size):
            if budget_left_usd is not None and result.cost_usd >= budget_left_usd:
                log.warning("Plafond de dépense atteint : %d annonce(s) reportée(s).", len(ads) - start)
                result.failed.extend(ads[start:])
                break
            batch = ads[start:start + self.batch_size]
            try:
                self._analyze_batch(batch, result)
            except FatalAPIError as exc:
                log.error("Analyse interrompue : %s", exc)
                done = {id(a) for a in result.analyzed + result.failed}
                result.failed.extend(a for a in ads[start:] if id(a) not in done)
                break
            log.info(
                "Lot %d-%d analysé — %d in / %d out tokens ≈ %.4f $",
                start + 1, start + len(batch), result.input_tokens, result.output_tokens, result.cost_usd,
            )
        return result


def analyze(ads: list[Ad], config: dict[str, Any], budget_left_usd: float | None = None) -> AnalysisResult:
    """Point d'entrée utilisé par le pipeline."""
    if not ads:
        return AnalysisResult()
    if not env("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY manquante : analyse impossible.")
        return AnalysisResult(failed=list(ads))
    try:
        analyzer = Analyzer(config)
    except Exception as exc:  # SDK absent, configuration invalide…
        log.error("Impossible d'initialiser l'analyseur : %s", exc)
        return AnalysisResult(failed=list(ads))
    return analyzer.analyze(ads, budget_left_usd)
