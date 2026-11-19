"""Notifications Telegram et/ou Discord des bonnes affaires (score ≥ seuil).

Chaque canal s'active dès que ses variables d'environnement sont renseignées ;
sans canal, les messages s'affichent dans la console. Rate-limit (HTTP 429)
respecté, 3 tentatives par message, et une erreur d'envoi n'interrompt rien.
"""

from __future__ import annotations

import html
import logging
import time
from typing import Any

import requests

from .config import env
from .models import Ad

log = logging.getLogger(__name__)

TIMEOUT = 15
MESSAGE_LIMIT = 3500  # Telegram : 4096 caractères max par message
MAX_ATTEMPTS = 3
DEAL_LABELS = {"reparation": "🔧 Réparation", "revente_directe": "💎 Revente directe"}
CONFIDENCE_LABELS = {"haute": "confiance haute", "moyenne": "confiance moyenne", "faible": "⚠️ confiance faible"}


# --------------------------------------------------------------------------- #
#  Mise en forme
# --------------------------------------------------------------------------- #
def _eur(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:.0f} €" if float(value).is_integer() else f"{value:.2f} €".replace(".", ",")


def _score_emoji(score: int) -> str:
    return "🔥" if score >= 9 else "🎯" if score >= 8 else "👍"


def _price_line(ad: Ad) -> str:
    line = _eur(ad.price)
    if ad.shipping:
        line += f" + {_eur(ad.shipping)} port = {_eur(ad.total_price)}"
    if ad.price_mode == "enchere":
        line += " (enchère en cours)"
    return line


def format_telegram(ad: Ad) -> str:
    a, e = ad.analysis or {}, html.escape
    label = DEAL_LABELS.get(ad.deal_type, "")
    header = f"{_score_emoji(ad.score)} <b>{ad.score}/10</b> · {label} · {e(ad.category or '')}"
    lines = [header, f"<b>{e(ad.title)}</b>", "", f"💶 Prix : {_price_line(ad)}"]
    delivery = " · 📦 livraison possible" if ad.delivery_possible else ""
    lines.append(f"📍 {e(ad.location or 'lieu ?')} · <i>{e(ad.source)}</i>{delivery}")
    if ad.deal_type == "reparation":
        lines.append(f"🔧 Panne probable : {e(a.get('panne_probable', '?'))}")
        part, resale = _eur(a.get("cout_piece_estime")), _eur(a.get("valeur_revente_estimee"))
        lines.append(f"🧩 Pièce ≈ {part} · 📈 Revente ≈ {resale}")
    else:
        lines.append(f"📈 Revente ≈ {_eur(a.get('valeur_revente_estimee'))} (en l'état)")
    lines += [
        f"💰 <b>Marge nette ≈ {_eur(a.get('marge_nette_estimee'))}</b>"
        f" ({CONFIDENCE_LABELS.get(a.get('confiance'), '')})",
        f"💬 {e(a.get('justification_courte', ''))}",
    ]
    if a.get("question_vendeur"):
        lines.append(f"❓ À demander : {e(a['question_vendeur'])}")
    lines += ["", f'🔗 <a href="{e(ad.url, quote=True)}">Voir l\'annonce</a>']
    return "\n".join(lines)


def format_discord(ad: Ad) -> dict[str, Any]:
    a = ad.analysis or {}
    color = 0xE74C3C if ad.score >= 9 else 0xF39C12 if ad.score >= 8 else 0x2ECC71
    fields = [
        {"name": "💶 Prix", "value": _price_line(ad), "inline": True},
        {"name": "💰 Marge nette", "value": _eur(a.get("marge_nette_estimee")), "inline": True},
        {"name": "📈 Revente", "value": _eur(a.get("valeur_revente_estimee")), "inline": True},
    ]
    if ad.deal_type == "reparation":
        fields += [
            {"name": "🔧 Panne probable", "value": (a.get("panne_probable") or "?")[:200], "inline": True},
            {"name": "🧩 Pièce", "value": _eur(a.get("cout_piece_estime")), "inline": True},
        ]
    fields.append({"name": "📍 Lieu", "value": (ad.location or "?")[:100], "inline": True})
    if a.get("question_vendeur"):
        fields.append({"name": "❓ À demander", "value": a["question_vendeur"][:200], "inline": False})
    return {
        "title": f"{_score_emoji(ad.score)} {ad.score}/10 · {DEAL_LABELS.get(ad.deal_type, '')} — {ad.title}"[:256],
        "url": ad.url,
        "color": color,
        "description": f"{a.get('justification_courte', '')} ({CONFIDENCE_LABELS.get(a.get('confiance'), '')})"[:500],
        "fields": fields,
        "footer": {"text": f"Deal Sniper · {ad.category} · {ad.source}"},
    }


def _digest_line(ad: Ad, html_mode: bool) -> str:
    """Une ligne de récapitulatif : note, titre cliquable, prix, lieu, marge."""
    a = ad.analysis or {}
    margin = _eur(a.get("marge_nette_estimee"))
    delivery = " · 📦" if ad.delivery_possible else ""
    details = f"{_price_line(ad)} · {ad.location or 'lieu ?'}{delivery} · marge ≈ {margin}"
    reason = a.get("justification_courte", "")
    if html_mode:
        e = html.escape
        return (f'{_score_emoji(ad.score) if ad.score >= 7 else "•"} <b>{ad.score}/10</b> '
                f'<a href="{e(ad.url, quote=True)}">{e(ad.title[:70])}</a>\n   {e(details)}\n   <i>{e(reason)}</i>')
    return f"• **{ad.score}/10** [{ad.title[:70]}]({ad.url})\n   {details}\n   _{reason}_"


def _chunks(header: str, lines: list[str]) -> list[str]:
    """Découpe un récapitulatif en messages de taille acceptable."""
    messages, current = [], header
    for line in lines:
        if len(current) + len(line) + 2 > MESSAGE_LIMIT:
            messages.append(current)
            current = header + " (suite)"
        current += "\n\n" + line
    messages.append(current)
    return messages


def format_console(ad: Ad) -> str:
    a = ad.analysis or {}
    return (
        f"\n{_score_emoji(ad.score)} {ad.score}/10 | {DEAL_LABELS.get(ad.deal_type, '')} | {ad.title}\n"
        f"   {_price_line(ad)} | {ad.location or 'lieu ?'} | marge ≈ {_eur(a.get('marge_nette_estimee'))}\n"
        f"   {a.get('justification_courte', '')}\n   {ad.url}"
    )


# --------------------------------------------------------------------------- #
#  Envoi
# --------------------------------------------------------------------------- #
def _post(url: str, payload: dict[str, Any], channel: str) -> bool:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, json=payload, timeout=TIMEOUT)
        except requests.RequestException as exc:
            log.warning("%s : erreur réseau (%s), tentative %d/%d", channel, exc, attempt, MAX_ATTEMPTS)
            time.sleep(2 * attempt)
            continue
        if resp.status_code == 429:
            try:
                body = resp.json()
                wait = float(body.get("retry_after") or body.get("parameters", {}).get("retry_after") or 2)
            except ValueError:
                wait = 2.0
            time.sleep(min(wait, 30))
            continue
        if resp.ok:
            return True
        if resp.status_code >= 500:
            time.sleep(2 * attempt)
            continue
        # Le token n'apparaît jamais dans ce message (seul le code et la réponse du service).
        log.error("%s : échec HTTP %s — %s", channel, resp.status_code, resp.text[:200])
        return False
    return False


class Notifier:
    def __init__(self) -> None:
        self.tg_token = env("TELEGRAM_BOT_TOKEN")
        self.tg_chat = env("TELEGRAM_CHAT_ID")
        self.discord_url = env("DISCORD_WEBHOOK_URL")

    @property
    def channels(self) -> list[str]:
        out = []
        if self.tg_token and self.tg_chat:
            out.append("telegram")
        if self.discord_url:
            out.append("discord")
        return out

    def _telegram(self, text_html: str) -> bool:
        return _post(
            f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
            {"chat_id": self.tg_chat, "text": text_html, "parse_mode": "HTML"},
            "Telegram",
        )

    def _discord(self, content: str | None = None, embeds: list[dict] | None = None) -> bool:
        payload: dict[str, Any] = {"username": "Deal Sniper"}
        if content:
            payload["content"] = content[:2000]
        if embeds:
            payload["embeds"] = embeds
        return _post(self.discord_url, payload, "Discord")

    def send_ad(self, ad: Ad) -> bool:
        """True si au moins un canal a accepté le message."""
        if not self.channels:
            print(format_console(ad))
            return True
        ok = False
        if "telegram" in self.channels:
            ok |= self._telegram(format_telegram(ad))
        if "discord" in self.channels:
            ok |= self._discord(embeds=[format_discord(ad)])
        return ok

    def send_digest(self, header: str, ads: list[Ad]) -> bool:
        """Un ou quelques messages regroupant plusieurs annonces (au lieu d'un message par annonce)."""
        if not self.channels:
            print(header)
            for ad in ads:
                print(_digest_line(ad, html_mode=False))
            return True
        ok = False
        if "telegram" in self.channels:
            for message in _chunks(f"<b>{html.escape(header)}</b>", [_digest_line(a, True) for a in ads]):
                ok |= self._telegram(message)
                time.sleep(0.5)
        if "discord" in self.channels:
            for message in _chunks(f"**{header}**", [_digest_line(a, False) for a in ads]):
                ok |= self._discord(content=message[:2000])
        return ok

    def send_text(self, text: str) -> bool:
        if not self.channels:
            print(text)
            return True
        ok = False
        if "telegram" in self.channels:
            ok |= self._telegram(html.escape(text))
        if "discord" in self.channels:
            ok |= self._discord(content=text)
        return ok


def _rank(ads: list[Ad]) -> list[Ad]:
    return sorted(ads, key=lambda a: (-a.score, -(a.analysis or {}).get("marge_nette_estimee", 0)))


def notify(ads: list[Ad], config: dict[str, Any]) -> list[Ad]:
    """Bonnes affaires (≥ min_score) : un message chacune. Annonces « à vérifier »
    (≥ watch_score) : un seul récapitulatif. Renvoie les bonnes affaires envoyées."""
    cfg = config["notifier"]
    min_score = int(cfg["min_score"])
    watch_score = cfg.get("watch_score")
    deal_types = set(cfg.get("deal_types") or DEAL_LABELS)
    analyzed = [a for a in ads if a.analysis and a.deal_type in deal_types]
    good = _rank([a for a in analyzed if a.score >= min_score])
    watch = _rank([a for a in analyzed if watch_score is not None and int(watch_score) <= a.score < min_score])
    notifier = Notifier()

    if watch:
        header = f"👀 À vérifier ({len(watch)} annonce(s) notée(s) {watch_score}-{min_score - 1}/10)"
        notifier.send_digest(header, watch)
    if not good:
        log.info("Aucune annonce ≥ %d/10 ; %d « à vérifier ».", min_score, len(watch))
        return []

    sent: list[Ad] = []
    max_per_run = int(cfg.get("max_per_run") or len(good))  # None = toutes
    for ad in good[:max_per_run]:
        try:
            if notifier.send_ad(ad):
                sent.append(ad)
            time.sleep(0.5)  # Telegram : ~1 message/seconde par conversation
        except Exception as exc:
            log.error("Notification impossible pour « %s » : %s", ad.title[:50], exc)
    if len(good) > max_per_run:
        extra = len(good) - max_per_run
        notifier.send_text(f"… et {extra} autre(s) bonne(s) affaire(s), voir l'interface Deal Sniper.")
    log.info("Notifications : %d envoyée(s) via %s", len(sent), ", ".join(notifier.channels) or "console")
    return sent


def demo_ad() -> Ad:
    """Fausse annonce utilisée par `test-notif`."""
    return Ad(
        title="[TEST] Lenovo ThinkPad T480 i5 16 Go — sans SSD",
        url="https://example.com/annonce-de-test",
        source="test", price=85.0, shipping=6.5, category="PC portables pros", location="Lyon 69003",
        analysis={
            "type_affaire": "reparation", "score_rentabilite": 8, "confiance": "haute",
            "panne_probable": "SSD manquant", "cout_piece_estime": 25, "valeur_revente_estimee": 220,
            "marge_nette_estimee": 82,
            "justification_courte": "Message de test : si vous le lisez, les notifications fonctionnent !",
            "question_vendeur": "Le chargeur d'origine est-il fourni ?",
        },
    )
