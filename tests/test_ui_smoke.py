"""Ouvre l'interface dans un vrai navigateur et vérifie qu'aucune erreur JavaScript ne survient.

Ignoré si Playwright ou son navigateur ne sont pas installés (chemin du navigateur réglable avec
PLAYWRIGHT_CHROMIUM_EXECUTABLE).
"""

import os
import threading

import pytest
from werkzeug.serving import make_server

from dealsniper.web.app import create_app


@pytest.fixture
def server(client):
    app_server = make_server("127.0.0.1", 0, create_app())
    thread = threading.Thread(target=app_server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{app_server.server_port}"
    app_server.shutdown()


def test_every_tab_loads_without_javascript_errors(server):
    sync_api = pytest.importorskip("playwright.sync_api")
    errors = []
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        except Exception as exc:  # navigateur absent
            pytest.skip(f"navigateur indisponible : {exc}")
        page = browser.new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(server)
        page.wait_for_selector("#ads .ad")  # les annonces de test s'affichent
        page.click("[data-info]")
        page.wait_for_selector("#details[open]")
        page.keyboard.press("Escape")
        for tab in ("purchases", "searches", "categories", "filters", "settings", "ads"):
            page.click(f".tab[data-tab={tab}]")
            page.wait_for_timeout(300)
        browser.close()
    assert errors == []
