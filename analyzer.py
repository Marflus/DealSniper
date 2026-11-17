"""
analyzer.py — Évaluation des annonces pré-filtrées par l'API Anthropic.

Stratégie « coût minimal » :
  * deux types d'affaires évalués : « reparation » (objet en panne) et
    « revente_directe » (objet fonctionnel vendu sous sa cote) ;
  * modèle économique (par défaut `claude-haiku-4-5`, modifiable dans config.yaml) ;
  * annonces envoyées **par paquets** (un seul prompt système pour N annonces) ;
  * descriptions tronquées à 600 caractères, format d'annonce ultra-compact ;
  * réponse contrainte par **structured outputs** (schéma JSON) → pas de texte
    superflu facturé, pas de JSON cassé à réparer ;
  * coût estimé à chaque lot à partir de `response.usage` et plafond journalier
    configurable (`max_daily_cost_usd`).

Robustesse :
  * le SDK réessaie automatiquement les erreurs temporaires (429, 5xx,
    surcharge, coupure réseau) avec backoff exponentiel (`max_retries`) ;
  * si un lot échoue malgré tout, ses annonces sont renvoyées dans `failed`
    et ne sont PAS marquées comme vues → elles seront retentées au prochain run ;
  * réponse tronquée (`max_tokens`) → le lot est coupé en deux et relancé ;
  * clé API invalide → arrêt propre de l'analyse (inutile d'insister).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from core import Ad, env, truncate

log = logging.getLogger("dealsniper.analyzer")

# Prix publics en $ par million de tokens (entrée, sortie).
# Surchargeables via `analyzer.pricing` dans config.yaml.
DEFAULT_PRICING = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}

SYSTEM_PROMPT = """Tu es expert en achat-revente d'électronique d'occasion (marché français : leboncoin, eBay, Back Market).
Pour chaque annonce, estime la rentabilité d'un achat pour revente. Deux types d'affaires :
- "reparation" : objet en panne ou incomplet, à remettre en état puis revendre.
- "revente_directe" : objet fonctionnel vendu nettement sous sa cote d'occasion, revendable tel quel.

Barème score_rentabilite (entier 1-10) :
- 8-10 : marge nette ≥ 40 € ET risque faible. Réparation : panne facile et peu coûteuse (pièce < 40 €, < 1 h), ex. PC pro sans disque/SSD ou sans OS, batterie HS, chargeur absent, port HDMI/USB-C dessoudé, rétroéclairage TV/écran, console qui surchauffe, outillage vendu sans batterie, nappe/clavier/écran courant. Revente directe : fonctionnel, au moins 30 % sous la cote, modèle recherché, annonce crédible (photos/état/facture évoqués).
- 4-7 : réparation moyenne, pièce chère, décote modeste, ou marge faible/incertaine.
- 1-3 : panne lourde ou inconnue (carte mère, oxydation, liquide, verrouillage iCloud/BIOS/compte), annonce trop vague, prix proche de la cote, ou prix anormalement bas sur un produit récent et recherché (arnaque probable).

Règles :
- Montants en euros. prix_total = prix + livraison.
- valeur_revente_estimee : prix d'occasion réaliste (après réparation le cas échéant), pas le prix neuf.
- marge_nette_estimee = valeur_revente_estimee - prix_total - cout_piece_estime - 10 % de frais de revente.
- Revente directe : panne_probable = "aucune", cout_piece_estime = 0 (sauf petit accessoire manquant).
- Dans le doute (infos manquantes, modèle imprécis), sois prudent et baisse le score.
- panne_probable : 8 mots max. justification_courte : 20 mots max.
- Renvoie une entrée par annonce, avec son id."""

# Schéma imposé à la réponse (structured outputs).
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "annonces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "type_affaire": {"type": "string", "enum": ["reparation", "revente_directe"]},
                    "score_rentabilite": {"type": "integer"},
                    "panne_probable": {"type": "string"},
                    "cout_piece_estime": {"type": "number"},
                    "valeur_revente_estimee": {"type": "number"},
                    "marge_nette_estimee": {"type": "number"},
                    "justification_courte": {"type": "string"},
                },
                "required": [
                    "id",
                    "type_affaire",
                    "score_rentabilite",
                    "panne_probable",
                    "cout_piece_estime",
                    "valeur_revente_estimee",
                    "marge_nette_estimee",
                    "justification_courte",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["annonces"],
    "additionalProperties": False,
}


DEAL_TYPES = ("reparation", "revente_directe")


class FatalAPIError(RuntimeError):
    """Erreur non récupérable (clé invalide, accès refusé, crédit épuisé...)."""


@dataclass
class AnalysisResult:
    analyzed: list[Ad] = field(default_factory=list)
    failed: list[Ad] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    api_calls: int = 0


# --------------------------------------------------------------------------- #
#  Construction du prompt
# --------------------------------------------------------------------------- #
def _fmt_money(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"


def format_ad(idx: int, ad: Ad, max_chars: int) -> str:
    """Une annonce = une ligne compacte. Chaque caractère économisé est un token en moins."""
    parts = [f"#{idx} [{ad.category or '?'}] {ad.title}"]
    price = f"{_fmt_money(ad.price)} €"
    if ad.shipping:
        price += f" + {_fmt_money(ad.shipping)} € port"
    parts.append(price)
    if ad.signals:
        parts.append("signaux: " + ", ".join(ad.signals))
    desc = truncate(ad.description.replace(ad.title, "").strip(), max_chars)
    if desc:
        parts.append(desc)
    return " | ".join(parts)


def build_user_prompt(ads: list[Ad], max_chars: int) -> str:
    return "Annonces :\n" + "\n".join(format_ad(i, ad, max_chars) for i, ad in enumerate(ads, 1))


# --------------------------------------------------------------------------- #
#  Analyse
# --------------------------------------------------------------------------- #
def _extract_json(text: str) -> dict[str, Any]:
    """Parse la réponse ; tolère un éventuel texte autour du JSON (mode dégradé)."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def _clamp_analysis(raw: dict[str, Any]) -> dict[str, Any]:
    """Assainit les valeurs renvoyées (types, bornes, longueur des textes)."""

    def num(key: str) -> float:
        try:
            return round(float(raw.get(key) or 0), 2)
        except (TypeError, ValueError):
            return 0.0

    try:
        score = int(round(float(raw.get("score_rentabilite", 1))))
    except (TypeError, ValueError):
        score = 1
    deal_type = str(raw.get("type_affaire", "reparation")).strip().lower()
    if deal_type not in DEAL_TYPES:
        deal_type = "revente_directe" if "direct" in deal_type else "reparation"
    return {
        "type_affaire": deal_type,
        "score_rentabilite": max(1, min(10, score)),
        "panne_probable": str(raw.get("panne_probable", ""))[:120],
        "cout_piece_estime": num("cout_piece_estime"),
        "valeur_revente_estimee": num("valeur_revente_estimee"),
        "marge_nette_estimee": num("marge_nette_estimee"),
        "justification_courte": str(raw.get("justification_courte", ""))[:240],
    }


class Analyzer:
    def __init__(self, config: dict[str, Any]):
        import anthropic

        self._anthropic = anthropic
        acfg = config.get("analyzer", {})
        self.model: str = acfg.get("model", "claude-haiku-4-5")
        self.batch_size: int = max(1, int(acfg.get("batch_size", 10)))
        self.max_chars: int = int(acfg.get("max_description_chars", 600))
        self.max_tokens: int = int(acfg.get("max_tokens", 4096))
        self.effort: str | None = acfg.get("effort")  # ex. "low" pour les modèles qui le supportent
        self.use_structured_output: bool = bool(acfg.get("structured_output", True))
        pricing = {**DEFAULT_PRICING, **{k: tuple(v) for k, v in (acfg.get("pricing") or {}).items()}}
        self.price_in, self.price_out = pricing.get(self.model, (1.0, 5.0))

        api_key = env("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(
            api_key=api_key,
            max_retries=int(acfg.get("max_retries", 4)),  # backoff exponentiel géré par le SDK
            timeout=float(acfg.get("timeout_seconds", 90)),
        )

    # -- appel API ---------------------------------------------------------- #
    def _call(self, ads: list[Ad], result: AnalysisResult) -> tuple[dict[str, Any], str]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": build_user_prompt(ads, self.max_chars)}],
        }
        output_config: dict[str, Any] = {}
        if self.use_structured_output:
            output_config["format"] = {"type": "json_schema", "schema": RESPONSE_SCHEMA}
        if self.effort:
            output_config["effort"] = self.effort
        if output_config:
            params["output_config"] = output_config
        else:
            params["system"] += "\nRéponds UNIQUEMENT par un objet JSON {\"annonces\": [...]} sans texte autour."

        response = self.client.messages.create(**params)
        result.api_calls += 1
        usage = response.usage
        result.input_tokens += usage.input_tokens
        result.output_tokens += usage.output_tokens
        result.cost_usd += (usage.input_tokens * self.price_in + usage.output_tokens * self.price_out) / 1e6

        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        return (_extract_json(text) if text else {}), response.stop_reason or ""

    def _analyze_batch(self, ads: list[Ad], result: AnalysisResult) -> None:
        anthropic = self._anthropic
        try:
            data, stop_reason = self._call(ads, result)
        except anthropic.AuthenticationError as exc:
            raise FatalAPIError(f"clé API refusée : {exc}") from exc
        except anthropic.PermissionDeniedError as exc:
            raise FatalAPIError(f"accès refusé : {exc}") from exc
        except anthropic.BadRequestError as exc:
            msg = str(exc).lower()
            if "credit" in msg or "billing" in msg:
                raise FatalAPIError(f"crédit API insuffisant : {exc}") from exc
            if self.use_structured_output and ("output_config" in msg or "format" in msg or "schema" in msg):
                # Modèle sans structured outputs : on bascule en mode JSON « par consigne »
                log.warning("Structured outputs refusés par %s → mode JSON simple.", self.model)
                self.use_structured_output = False
                return self._analyze_batch(ads, result)
            log.error("Requête refusée (400) pour un lot de %d annonce(s) : %s", len(ads), exc)
            result.failed.extend(ads)
            return
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
            # Erreur temporaire persistante malgré les retries du SDK : on retentera au prochain run.
            log.error("Erreur API temporaire (%s) — lot de %d reporté.", type(exc).__name__, len(ads))
            result.failed.extend(ads)
            return
        except (json.JSONDecodeError, ValueError) as exc:
            log.error("Réponse JSON illisible (%s) — lot de %d reporté.", exc, len(ads))
            result.failed.extend(ads)
            return

        if stop_reason == "max_tokens" and len(ads) > 1:
            log.warning("Réponse tronquée : lot de %d coupé en deux.", len(ads))
            mid = len(ads) // 2
            self._analyze_batch(ads[:mid], result)
            self._analyze_batch(ads[mid:], result)
            return
        if stop_reason == "refusal":
            log.warning("Le modèle a refusé d'analyser ce lot de %d annonce(s).", len(ads))
            result.failed.extend(ads)
            return

        by_id = {}
        for item in data.get("annonces", []) if isinstance(data, dict) else []:
            try:
                by_id[int(item.get("id"))] = item
            except (TypeError, ValueError):
                continue
        for idx, ad in enumerate(ads, 1):
            if idx in by_id:
                ad.analysis = _clamp_analysis(by_id[idx])
                result.analyzed.append(ad)
            else:
                log.warning("Pas de réponse pour « %s » — reportée.", ad.title[:50])
                result.failed.append(ad)

    def analyze(self, ads: list[Ad], budget_left_usd: float | None = None) -> AnalysisResult:
        """Analyse toutes les annonces, lot par lot. Ne lève que `FatalAPIError`."""
        result = AnalysisResult()
        for start in range(0, len(ads), self.batch_size):
            batch = ads[start : start + self.batch_size]
            if budget_left_usd is not None and result.cost_usd >= budget_left_usd:
                log.warning("Budget API journalier atteint — %d annonce(s) reportée(s).", len(ads) - start)
                result.failed.extend(ads[start:])
                break
            try:
                self._analyze_batch(batch, result)
            except FatalAPIError as exc:
                # Inutile d'insister ; on garde les lots déjà analysés (et payés).
                log.error("Analyse interrompue : %s", exc)
                done = {id(a) for a in result.analyzed} | {id(a) for a in result.failed}
                result.failed.extend(a for a in ads[start:] if id(a) not in done)
                break
            log.info(
                "Lot %d-%d analysé — cumul %d in / %d out tokens ≈ %.4f $",
                start + 1, start + len(batch), result.input_tokens, result.output_tokens, result.cost_usd,
            )
        return result


def analyze(ads: list[Ad], config: dict[str, Any], budget_left_usd: float | None = None) -> AnalysisResult:
    """Raccourci fonctionnel utilisé par main.py."""
    if not ads:
        return AnalysisResult()
    if not env("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY manquante — analyse IA impossible.")
        return AnalysisResult(failed=list(ads))
    try:
        analyzer = Analyzer(config)
    except Exception as exc:  # SDK absent, configuration invalide...
        log.error("Impossible d'initialiser l'analyseur : %s", exc)
        return AnalysisResult(failed=list(ads))
    return analyzer.analyze(ads, budget_left_usd)
