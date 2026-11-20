"""Protection de l'interface locale.

1. Requêtes d'un autre site (CSRF) : une page web ouverte dans votre navigateur ne doit pas pouvoir
   piloter Deal Sniper (réécrire les clés, lancer des analyses payantes…). Toute requête qui modifie
   quelque chose doit venir de l'interface elle-même (en-têtes Origin / Referer / Sec-Fetch-Site).
2. Rebinding DNS : un nom de domaine piégé qui pointe vers 127.0.0.1 est refusé ; seuls « localhost »,
   les adresses IP et les noms de DEALSNIPER_ALLOWED_HOSTS sont acceptés.
3. Réseau local (`--host 0.0.0.0`) : un appareil autre que ce PC doit présenter le jeton d'accès
   (lien affiché au démarrage, mémorisé ensuite dans un cookie).
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
import socket
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Response, request

COOKIE = "dealsniper_token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOOPBACK = {"127.0.0.1", "::1"}


def ui_token(data_dir: Path) -> str:
    """Jeton d'accès depuis le réseau local : créé une fois, gardé dans data/ui_token."""
    path = data_dir / "ui_token"
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 16:
            return token
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(18)
    path.write_text(token + "\n", encoding="utf-8")
    return token


def lan_ip() -> str | None:
    """Adresse de ce PC sur le réseau local (aucun paquet envoyé : simple choix de route)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))  # adresse de documentation : jamais contactée
            return sock.getsockname()[0]
    except OSError:
        return None


def _hostname(host: str) -> str:
    """« 192.168.1.10:5000 » → « 192.168.1.10 » ; « [::1]:5000 » → « ::1 »."""
    return urlsplit(f"//{host}").hostname or ""


def host_allowed(host: str) -> bool:
    name = _hostname(host).lower()
    extra = {h.strip().lower() for h in os.getenv("DEALSNIPER_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if name in {"localhost", *extra}:
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def same_origin() -> bool:
    """Vrai si la requête vient de l'interface elle-même (ou d'un client sans navigateur)."""
    if request.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
        return False
    for header in ("Origin", "Referer"):
        value = request.headers.get(header)
        if value and value != "null":
            return urlsplit(value).netloc.lower() == request.host.lower()
        if value == "null":  # page locale, iframe isolée… : origine inconnue, refusée
            return False
    return True  # ni Origin ni Referer : pas un navigateur (curl, tests)


def protect(app: Flask, token: str) -> None:
    """Branche les trois protections sur l'application."""

    @app.before_request
    def _guard() -> Response | None:
        if not host_allowed(request.host):
            return Response("Hôte non autorisé.", 403)
        if request.method in UNSAFE_METHODS and not same_origin():
            return Response("Requête refusée : elle ne vient pas de l'interface Deal Sniper.", 403)
        if request.remote_addr in LOOPBACK:
            return None  # ce PC : toujours autorisé
        given = request.args.get("token") or request.cookies.get(COOKIE) or ""
        if hmac.compare_digest(given.encode(), token.encode()):
            return None
        return Response("<h1>Accès protégé</h1><p>Ouvrez le lien avec le jeton affiché au démarrage de "
                        "Deal Sniper (fenêtre de <code>start_ui.bat</code>).</p>", 401, mimetype="text/html")

    @app.after_request
    def _remember(response: Response) -> Response:
        # Lien « ?token=… » ouvert une fois : le jeton est gardé dans un cookie (1 an).
        given = request.args.get("token")
        if given and request.remote_addr not in LOOPBACK and hmac.compare_digest(given.encode(), token.encode()):
            # Lax (pas Strict) : un lien ouvert depuis une autre appli (Telegram…) garde l'accès ; les requêtes
            # qui modifient quelque chose restent protégées par la vérification d'origine ci-dessus.
            response.set_cookie(COOKIE, token, max_age=365 * 24 * 3600, httponly=True, samesite="Lax")
        return response
