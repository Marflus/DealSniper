"""
notifier.py — Envoi des bonnes affaires (score >= seuil) sur Telegram et/ou Discord.

Deux types d'affaires : 🔧 réparation (objet en panne) et 💎 revente directe
(objet fonctionnel vendu sous sa cote) ; filtrables via `notifier.deal_types`.

Chaque canal est optionnel : il est activé dès que ses variables d'environnement
sont renseignées. Sans aucun canal, les alertes sont simplement affichées dans
la console (pratique pour tester).

Robustesse : gestion du rate-limit (HTTP 429 + `retry_after`), 3 tentatives
par message, et une erreur d'envoi n'interrompt jamais le programme.
"""

from __future__ import annotations

import html
import logging
import time
from typing import Any

import requests

from core import Ad, env

log = logging.getLogger("dealsniper.notifier")

TIMEOUT = 15
MAX_ATTEMPTS = 3


# --------------------------------------------------------------------------- #
#  Mise en forme
# --------------------------------------------------------------------------- #
def _eur(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:.0f} €" if float(value).is_integer() else f"{value:.2f} €".replace(".", ",")


def _score_emoji(score: int) -> str:
    return "🔥" if score >= 9 else "🎯" if score >= 8 else "👍"


DEAL_LABELS = {"reparation": "🔧 Réparation", "revente_directe": "💎 Revente directe"}


def _deal_label(ad: Ad) -> str:
    return DEAL_LABELS.get((ad.analysis or {}).get("type_affaire", "reparation"), "🔧 Réparation")


def _is_repair(ad: Ad) -> bool:
    return (ad.analysis or {}).get("type_affaire", "reparation") == "reparation"


def _price_line(ad: Ad) -> str:
    line = _eur(ad.price)
    if ad.shipping:
        line += f" + {_eur(ad.shipping)} port = {_eur(ad.total_price)}"
    return line


def format_telegram(ad: Ad) -> str:
    a = ad.analysis or {}
    e = html.escape
    score = a.get("score_rentabilite", 0)
    lines = [
        f"{_score_emoji(score)} <b>{score}/10</b> · {_deal_label(ad)} · {e(ad.category or '')} · <i>{e(ad.source)}</i>",
        f"<b>{e(ad.title)}</b>",
        "",
        f"💶 Prix : {_price_line(ad)}",
    ]
    if _is_repair(ad):
        lines += [
            f"🔧 Panne probable : {e(a.get('panne_probable', '?'))}",
            f"🧩 Pièce ≈ {_eur(a.get('cout_piece_estime'))} · 📈 Revente ≈ {_eur(a.get('valeur_revente_estimee'))}",
        ]
    else:
        lines.append(f"📈 Revente ≈ {_eur(a.get('valeur_revente_estimee'))} (en l'état)")
    lines += [
        f"💰 <b>Marge nette ≈ {_eur(a.get('marge_nette_estimee'))}</b>",
        f"💬 {e(a.get('justification_courte', ''))}",
        "",
        f'🔗 <a href="{e(ad.url, quote=True)}">Voir l\'annonce</a>',
    ]
    return "\n".join(lines)


def format_discord(ad: Ad) -> dict[str, Any]:
    a = ad.analysis or {}
    score = int(a.get("score_rentabilite", 0))
    color = 0xE74C3C if score >= 9 else 0xF39C12 if score >= 8 else 0x2ECC71
    return {
        "title": f"{_score_emoji(score)} {score}/10 · {_deal_label(ad)} — {ad.title}"[:256],
        "url": ad.url,
        "color": color,
        "description": (a.get("justification_courte") or "")[:500],
        "fields": [
            {"name": "💶 Prix", "value": _price_line(ad), "inline": True},
            {"name": "💰 Marge nette", "value": _eur(a.get("marge_nette_estimee")), "inline": True},
            {"name": "📈 Revente", "value": _eur(a.get("valeur_revente_estimee")), "inline": True},
        ]
        + (
            [
                {"name": "🔧 Panne probable", "value": (a.get("panne_probable") or "?")[:200], "inline": True},
                {"name": "🧩 Pièce", "value": _eur(a.get("cout_piece_estime")), "inline": True},
            ]
            if _is_repair(ad)
            else [{"name": "✅ État", "value": "Fonctionnel — revente en l'état", "inline": True}]
        )
        + [{"name": "📂 Catégorie", "value": f"{ad.category} · {ad.source}"[:200], "inline": True}],
        "footer": {"text": "Deal Sniper"},
    }


def format_console(ad: Ad) -> str:
    a = ad.analysis or {}
    return (
        f"\n{_score_emoji(a.get('score_rentabilite', 0))} {a.get('score_rentabilite')}/10 | {_deal_label(ad)} | {ad.title}\n"
        f"   {_price_line(ad)} | panne : {a.get('panne_probable')} | marge ≈ {_eur(a.get('marge_nette_estimee'))}\n"
        f"   {a.get('justification_courte')}\n   {ad.url}"
    )


# --------------------------------------------------------------------------- #
#  Envoi HTTP avec gestion du rate-limit
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
            log.info("%s : rate-limit, pause %.1fs", channel, wait)
            time.sleep(min(wait, 30))
            continue
        if resp.ok:
            return True
        if 500 <= resp.status_code < 600:
            time.sleep(2 * attempt)
            continue
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

    def send_telegram_text(self, text: str) -> bool:
        return _post(
            f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
            {"chat_id": self.tg_chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
            "Telegram",
        )

    def send_discord(self, content: str | None = None, embeds: list[dict] | None = None) -> bool:
        payload: dict[str, Any] = {"username": "Deal Sniper"}
        if content:
            payload["content"] = content[:2000]
        if embeds:
            payload["embeds"] = embeds
        return _post(self.discord_url, payload, "Discord")

    def send_ad(self, ad: Ad) -> bool:
        """Envoie une annonce sur tous les canaux. True si au moins un a réussi."""
        if not self.channels:
            print(format_console(ad))
            return True
        ok = False
        if "telegram" in self.channels:
            ok |= self.send_telegram_text(format_telegram(ad))
        if "discord" in self.channels:
            ok |= self.send_discord(embeds=[format_discord(ad)])
        return ok

    def send_text(self, text: str) -> None:
        """Message libre (résumé, erreur, test)."""
        if not self.channels:
            print(text)
        if "telegram" in self.channels:
            self.send_telegram_text(html.escape(text))
        if "discord" in self.channels:
            self.send_discord(content=text)


def notify(ads: list[Ad], config: dict[str, Any]) -> list[Ad]:
    """Notifie les annonces dont le score atteint le seuil. Renvoie celles envoyées."""
    ncfg = config.get("notifier", {})
    min_score = int(ncfg.get("min_score", 7))
    max_per_run = int(ncfg.get("max_per_run", 15))

    deal_types = set(ncfg.get("deal_types") or ["reparation", "revente_directe"])
    good = [
        a for a in ads
        if a.analysis
        and a.analysis.get("score_rentabilite", 0) >= min_score
        and a.analysis.get("type_affaire", "reparation") in deal_types
    ]
    good.sort(key=lambda a: (-a.analysis["score_rentabilite"], -a.analysis.get("marge_nette_estimee", 0)))
    if not good:
        log.info("Aucune annonce >= %d/10 à notifier.", min_score)
        return []

    notifier = Notifier()
    sent: list[Ad] = []
    for ad in good[:max_per_run]:
        try:
            if notifier.send_ad(ad):
                sent.append(ad)
            time.sleep(0.5)  # politesse envers les API (Telegram : ~1 msg/s par chat)
        except Exception as exc:
            log.error("Notification impossible pour « %s » : %s", ad.title[:50], exc)
    if len(good) > max_per_run:
        notifier.send_text(f"… et {len(good) - max_per_run} autre(s) bonne(s) affaire(s) : voir l'interface Deal Sniper.")
    log.info("Notifications : %d envoyée(s) via %s", len(sent), ", ".join(notifier.channels) or "console")
    return sent
