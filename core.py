"""
core.py — Briques communes à tous les modules de Deal Sniper.

Contient :
  * la dataclass `Ad` (une annonce normalisée, quelle que soit sa source) ;
  * le chargement de `config.yaml` et de `.env` ;
  * des utilitaires texte : nettoyage HTML, normalisation sans accents,
    extraction de prix et de frais de livraison ;
  * la configuration du logging.

Aucune dépendance réseau ici : tout est testable hors-ligne.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

import yaml
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_CONFIG_PATH = BASE_DIR / "config.yaml"

log = logging.getLogger("dealsniper")


# --------------------------------------------------------------------------- #
#  Modèle de données
# --------------------------------------------------------------------------- #
@dataclass
class Ad:
    """Une annonce, normalisée après collecte."""

    title: str
    url: str
    source: str                       # ex : "leboncoin", "ebay-rss", "agorastore"
    price: float | None = None        # prix affiché en euros (None = inconnu)
    shipping: float | None = None     # frais de livraison (None = inconnu/non précisé)
    description: str = ""             # texte court, nettoyé du HTML
    category: str | None = None       # rempli par filter.py
    signals: list[str] = field(default_factory=list)  # mots-clés « positifs » repérés (filter.py)
    collected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    # Rempli par analyzer.py
    analysis: dict[str, Any] | None = None

    @property
    def total_price(self) -> float | None:
        """Prix + livraison (la livraison inconnue compte pour 0)."""
        if self.price is None:
            return None
        return round(self.price + (self.shipping or 0.0), 2)

    @property
    def uid(self) -> str:
        """Identifiant stable servant au dédoublonnage (voir `ad_uid`)."""
        return ad_uid(self.url, self.title, self.price)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["uid"] = self.uid
        d["total_price"] = self.total_price
        return d


# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
def load_env() -> None:
    """Charge `.env` s'il existe (en CI les variables viennent des secrets)."""
    try:
        from dotenv import load_dotenv

        load_dotenv(BASE_DIR / ".env")
    except ImportError:  # python-dotenv absent : on se contente de l'environnement
        pass


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Lit `config.yaml` et applique des valeurs par défaut raisonnables."""
    path = Path(path or os.getenv("DEALSNIPER_CONFIG", DEFAULT_CONFIG_PATH))
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}

    cfg.setdefault("analyzer", {})
    cfg["analyzer"].setdefault("model", "claude-haiku-4-5")
    cfg["analyzer"].setdefault("batch_size", 10)
    cfg["analyzer"].setdefault("max_description_chars", 600)
    cfg["analyzer"].setdefault("max_ads_per_run", 60)
    cfg.setdefault("notifier", {})
    cfg["notifier"].setdefault("min_score", 7)
    cfg.setdefault("filters", {})
    cfg.setdefault("categories", [])
    cfg.setdefault("sources", {})
    cfg.setdefault("storage", {})
    return cfg


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Les bibliothèques HTTP sont très bavardes en DEBUG
    for noisy in ("httpx", "httpx2", "httpcore", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --------------------------------------------------------------------------- #
#  Texte
# --------------------------------------------------------------------------- #
_WS_RE = re.compile(r"\s+")


def clean_html(raw: str | None) -> str:
    """Supprime balises, scripts et styles ; compresse les espaces."""
    if not raw:
        return ""
    if "<" not in raw:  # déjà du texte brut
        return _WS_RE.sub(" ", raw).strip()
    try:
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "head", "title", "meta"]):
            tag.decompose()
        text = soup.get_text(" ")
    except Exception:  # HTML vraiment cassé : on retire grossièrement les balises
        text = re.sub(r"<[^>]+>", " ", raw)
    return _WS_RE.sub(" ", text).strip()


def normalize(text: str | None) -> str:
    """Minuscules + suppression des accents → comparaisons robustes.

    "Carte Mère HS" -> "carte mere hs"
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("’", "'").replace(" ", " ")
    return _WS_RE.sub(" ", text.lower()).strip()


def contains_keyword(haystack_normalized: str, keyword: str) -> bool:
    """Recherche d'un mot-clé (déjà ou non normalisé) avec frontières de mots.

    Les frontières évitent que "hs" matche "hstore" ou que "ps4" matche "ps45".
    """
    kw = normalize(keyword)
    if not kw:
        return False
    # On tolère les accords français : "oxyde" matche aussi "oxydee", "oxydes", "oxydees".
    suffix = r"(?:e|s|es|x)?" if kw[-1].isalpha() else ""
    pattern = r"(?<![a-z0-9])" + re.escape(kw) + suffix + r"(?![a-z0-9])"
    return re.search(pattern, haystack_normalized) is not None


def truncate(text: str, max_chars: int) -> str:
    """Tronque proprement sur un espace et ajoute une ellipse."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut + "…"


# --------------------------------------------------------------------------- #
#  Prix
# --------------------------------------------------------------------------- #
# Un montant : "1 234,56" / "1.234,56" / "45.00" / "45" / "45,-"
_AMOUNT = r"(\d{1,3}(?:[   .]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_PRICE_PATTERNS = [
    re.compile(_AMOUNT + r"\s*(?:€|eur\b|euros?\b)", re.IGNORECASE),
    re.compile(r"(?:€|eur\b)\s*" + _AMOUNT, re.IGNORECASE),
]
_SHIPPING_FREE_RE = re.compile(
    r"(livraison|frais de port|port|shipping|envoi)\s*(gratuite?|offerte?|free|inclus)",
    re.IGNORECASE,
)
_SHIPPING_RE = re.compile(
    r"(?:livraison|frais de port|frais d'envoi|envoi|shipping|postage)[^0-9€]{0,25}"
    r"(?:€\s*)?" + _AMOUNT,
    re.IGNORECASE,
)
# "+ 4,99 € livraison" ou "4,99 € de livraison" (mais PAS "40 € Livraison 5 €")
_SHIPPING_RE_PLUS = re.compile(
    r"\+\s*" + _AMOUNT + r"\s*(?:€|eur)\s*(?:de\s+)?(?:livraison|frais de port|port|shipping|postage)\b",
    re.IGNORECASE,
)
_SHIPPING_RE_DE = re.compile(
    _AMOUNT + r"\s*(?:€|eur)\s*de\s+(?:livraison|frais de port|port)\b",
    re.IGNORECASE,
)
_SHIPPING_REGEXES = (_SHIPPING_RE, _SHIPPING_RE_PLUS, _SHIPPING_RE_DE)


def parse_amount(raw: str) -> float | None:
    """Convertit "1 234,56" / "1.234,56" / "45.00" en float."""
    if not raw:
        return None
    s = raw.strip().replace(" ", " ").replace(" ", " ")
    s = s.replace(" ", "")
    if "," in s and "." in s:          # 1.234,56 -> séparateur de milliers = "."
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:                     # 45,90
        s = s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):  # 1.234 -> milliers
        s = s.replace(".", "")
    try:
        value = float(s)
    except ValueError:
        return None
    # garde-fou : un « prix » à 7 chiffres est probablement une référence produit
    return value if 0 <= value < 1_000_000 else None


def extract_shipping(text: str) -> float | None:
    """Repère des frais de livraison dans un texte libre (0.0 si gratuite)."""
    if not text:
        return None
    if _SHIPPING_FREE_RE.search(text):
        return 0.0
    for rx in _SHIPPING_REGEXES:
        m = rx.search(text)
        if m:
            return parse_amount(m.group(1))
    return None


def extract_price(text: str) -> float | None:
    """Premier montant en euros qui n'est pas un montant de livraison."""
    if not text:
        return None
    shipping_spans = []
    for rx in _SHIPPING_REGEXES:
        shipping_spans += [m.span(1) for m in rx.finditer(text)]
    candidates = []
    for rx in _PRICE_PATTERNS:
        for m in rx.finditer(text):
            if any(a <= m.start(1) < b for a, b in shipping_spans):
                continue
            candidates.append((m.start(), m.group(1)))
    for _, raw in sorted(candidates):
        value = parse_amount(raw)
        if value is not None:
            return value
    return None


# --------------------------------------------------------------------------- #
#  URL & identifiants
# --------------------------------------------------------------------------- #
# Paramètres d'URL qui contiennent souvent la vraie destination d'un lien de tracking
_REDIRECT_PARAMS = ("url", "u", "redirect", "redirect_url", "target", "dest", "destination", "link")
# Paramètres de tracking sans valeur pour l'identification d'une annonce
_TRACKING_PREFIXES = ("utm_", "xtor", "at_", "mc_", "fbclid", "gclid", "campid", "mkevt", "mkcid", "mkrid", "_trk", "hash")


def unwrap_tracking_url(url: str) -> str:
    """Si `url` est un lien de redirection d'emailing, renvoie la vraie cible."""
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        for key in _REDIRECT_PARAMS:
            for value in qs.get(key, []):
                value = unquote(value)
                if value.startswith("http"):
                    return value
    except Exception:
        pass
    return url


def canonical_url(url: str) -> str:
    """URL sans fragment ni paramètres de tracking (pour le dédoublonnage)."""
    try:
        p = urlparse(url.strip())
        kept = [
            part
            for part in p.query.split("&")
            if part and not part.lower().startswith(_TRACKING_PREFIXES)
        ]
        return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", "&".join(kept), ""))
    except Exception:
        return url.strip()


_ID_PATTERNS = [
    re.compile(r"leboncoin\.fr/(?:ad/[^/]+/|[^/]+/)(\d{6,})"),  # leboncoin.fr/ad/informatique/2512345678
    re.compile(r"ebay\.[a-z.]+/itm/(?:[^/]+/)?(\d{9,})"),        # ebay.fr/itm/123456789012
]


def ad_uid(url: str, title: str = "", price: float | None = None) -> str:
    """Identifiant stable d'une annonce.

    On privilégie l'identifiant natif de la plateforme (robuste aux variations
    d'URL), sinon un hash de l'URL canonique, sinon un hash titre+prix.
    """
    for rx in _ID_PATTERNS:
        m = rx.search(url or "")
        if m:
            host = "lbc" if "leboncoin" in url else "ebay"
            return f"{host}:{m.group(1)}"
    basis = canonical_url(url) if url else f"{normalize(title)}|{price}"
    return "h:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def env(name: str, default: str | None = None) -> str | None:
    """Lit une variable d'environnement en ignorant les valeurs vides."""
    value = os.getenv(name)
    return value if value not in (None, "") else default
