"""Notifications Telegram et Discord (console si aucun canal n'est configuré)."""

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


def _eur(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:.0f} €" if float(value).is_integer() else f"{value:.2f} €".replace(".", ",")


def _summary(ad: Ad) -> dict[str, str]:
    """Les 5 informations d'une notification : titre, achat, revente (+ marge), panne, lien."""
    a = ad.analysis or {}
    buy = _eur(ad.price) + (f" + {_eur(ad.shipping)} port" if ad.shipping else "")
    if ad.price_mode == "enchere":
        buy += " (enchère en cours)"
    margin = a.get("marge_nette_estimee")
    margin_txt = f"{'+' if (margin or 0) >= 0 else ''}{_eur(margin)}" if margin is not None else "?"
    fault = a.get("panne_probable") or "?"
    ease = a.get("facilite_reparation")
    return {
        "title": ad.title,
        "buy": buy,
        "resale": f"{_eur(a.get('valeur_revente_estimee'))} (marge {margin_txt})",
        "fault": "aucune (revente en l'état)" if fault.lower() == "aucune"
        else f"{fault} (facilité {ease}/10)" if ease else fault,
        "url": ad.url,
    }


def format_telegram(ad: Ad) -> str:
    s, e = _summary(ad), html.escape
    return (
        f"<b>{e(s['title'])}</b>\n"
        f"💶 Achat : {e(s['buy'])}\n"
        f"📈 Revente : {e(s['resale'])}\n"
        f"🔧 Panne : {e(s['fault'])}\n"
        f'🔗 <a href="{e(s["url"], quote=True)}">Voir l\'annonce</a>'
    )


def format_discord(ad: Ad) -> dict[str, Any]:
    s = _summary(ad)
    embed: dict[str, Any] = {
        "title": s["title"][:256],
        "url": s["url"],
        "color": 0x16A34A,
        "description": f"💶 Achat : {s['buy']}\n📈 Revente : {s['resale']}\n🔧 Panne : {s['fault']}"[:2000],
    }
    if ad.image:
        embed["thumbnail"] = {"url": ad.image}
    return embed


def format_console(ad: Ad) -> str:
    s = _summary(ad)
    return f"\n{s['title']}\n   Achat {s['buy']} · Revente {s['resale']} · Panne {s['fault']}\n   {s['url']}"


def _digest_line(ad: Ad, html_mode: bool) -> str:
    s = _summary(ad)
    if html_mode:
        e = html.escape
        return (f'• <a href="{e(s["url"], quote=True)}">{e(s["title"][:70])}</a>\n'
                f"   Achat {e(s['buy'])} · Revente {e(s['resale'])} · {e(s['fault'])}")
    return f"• [{s['title'][:70]}]({s['url']})\n   Achat {s['buy']} · Revente {s['resale']} · {s['fault']}"


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
    deal_types = set(cfg.get("deal_types") or ("reparation", "revente_directe"))
    min_margin, min_ease = cfg.get("min_margin"), cfg.get("min_ease")

    def wanted(ad: Ad) -> bool:
        if not ad.analysis or ad.deal_type not in deal_types:
            return False
        margin = ad.analysis.get("marge_nette_estimee")
        if min_margin is not None and (margin is None or margin < float(min_margin)):
            return False
        ease = ad.analysis.get("facilite_reparation")  # ne concerne que les objets à réparer
        if min_ease is not None and ad.deal_type == "reparation" and (ease or 0) < int(min_ease):
            return False
        return True

    analyzed = [a for a in ads if wanted(a)]
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
    for ad in good:
        try:
            if notifier.send_ad(ad):
                sent.append(ad)
            time.sleep(0.5)  # Telegram : ~1 message/seconde par conversation
        except Exception as exc:
            log.error("Notification impossible pour « %s » : %s", ad.title[:50], exc)
    log.info("Notifications : %d envoyée(s) via %s", len(sent), ", ".join(notifier.channels) or "console")
    return sent


def demo_ad() -> Ad:
    """Fausse annonce utilisée par `test-notif`."""
    return Ad(
        title="[TEST] Lenovo ThinkPad T480 i5 16 Go — sans SSD",
        url="https://example.com/annonce-de-test",
        source="test", price=85.0, shipping=6.5, category="PC portables pros", location="Lyon 69003",
        analysis={
            "type_affaire": "reparation", "score_rentabilite": 8, "facilite_reparation": 9,
            "panne_probable": "SSD manquant", "cout_piece_estime": 25, "valeur_revente_estimee": 220,
            "marge_nette_estimee": 82,
        },
    )
