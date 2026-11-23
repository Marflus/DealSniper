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


def _browser(p):
    try:
        return p.chromium.launch(executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
    except Exception as exc:  # navigateur absent
        pytest.skip(f"navigateur indisponible : {exc}")


def test_main_interactions(server):
    """Parcours réel : filtres reliés recherche ↔ annonces, négociation, achat avec temps, aucune requête refusée."""
    sync_api = pytest.importorskip("playwright.sync_api")
    errors, refused, reloads = [], [], []
    with sync_api.sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("response", lambda r: refused.append((r.status, r.url)) if r.status in (401, 403, 500) else None)
        page.on("request", lambda r: reloads.append(r.url) if "/api/ads?" in r.url else None)
        page.goto(server)
        page.wait_for_selector("#ads .ad")

        # Filtre restauré au rechargement → reflété dans la recherche ponctuelle.
        page.select_option("#f-delivery", "ship")
        page.reload()
        page.wait_for_selector("#ads")
        assert page.input_value("#qs-delivery") == "ship"
        page.click("#reset-filters")

        # Zone tapée lettre par lettre : un seul rechargement, une fois la saisie finie.
        page.wait_for_timeout(300)
        reloads.clear()
        page.type("#f-near", "Lyon", delay=40)
        page.wait_for_timeout(900)
        assert len(reloads) == 1 and "near=Lyon" in reloads[0]

        # Fiche : message de négociation prêt à copier (requête POST protégée par l'origine : acceptée).
        page.click("#reset-filters")
        page.wait_for_selector("#ads .ad")
        page.click("#ads [data-info]")
        page.click("#details [data-nego]")
        page.wait_for_selector("#details [data-nego-text]")
        assert page.input_value("#details [data-nego-text]").startswith("Bonjour")
        page.click("#details [data-fav]")
        page.keyboard.press("Escape")

        # Achat ajouté à la main avec le temps passé, puis revendu : rentabilité horaire affichée.
        page.click(".tab[data-tab=purchases]")
        page.fill("#p-add [name=title]", "Test navigateur")
        page.fill("#p-add [name=buy_price]", "40")
        page.fill("#p-add [name=hours_spent]", "2")
        page.click("#p-add button.primary")
        page.wait_for_selector('#p-list [data-key="sold_price"]')
        sold = page.locator('#p-list [data-key="sold_price"]').first
        sold.fill("100")
        sold.dispatch_event("change")
        page.wait_for_function("document.querySelector('#p-summary').innerText.includes('€/h')")
        browser.close()
    assert errors == [] and refused == []


def test_saved_searches_tab(server):
    """Onglet Enregistrées : exemples affichés, nouvelle recherche créée, enregistrée, lancée."""
    sync_api = pytest.importorskip("playwright.sync_api")
    errors, posted = [], []
    with sync_api.sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("request", lambda r: posted.append(r.post_data_json) if r.url.endswith("/api/search-now") else None)
        page.goto(server)
        page.click(".tab[data-tab=saved]")
        page.wait_for_selector("#saved-list [data-saved]")
        assert page.locator("#saved-list [data-saved]").count() == 2  # exemples fournis
        page.click("#add-saved")
        card = page.locator("#saved-list [data-saved]").last
        card.locator('[data-s="name"]').fill("Consoles portables")
        tags = card.locator('[data-s="queries"] .tag-input')
        tags.fill("steam deck")
        tags.press("Enter")
        tags.fill("switch oled")
        tags.press("Enter")
        sync_api.expect(card.locator("[data-s-hint]")).to_contain_text("2 modèles")
        page.keyboard.press("Control+s")
        page.wait_for_function("document.querySelector('#save-status').innerText.includes('Tout est enregistré')")
        page.reload()
        page.click(".tab[data-tab=saved]")
        page.wait_for_selector("#saved-list [data-saved]:nth-child(3)")
        page.locator("#saved-list [data-saved]").last.locator("[data-run-saved]").click()
        page.wait_for_timeout(500)
        browser.close()
    assert errors == []
    assert posted and posted[-1]["queries"] == ["steam deck", "switch oled"]
    assert posted[-1]["name"] == "Consoles portables"


def test_saved_search_loop_toggle(server):
    """« En boucle » : intervalle limité à 30 min, enregistré, puis état du prochain scan affiché."""
    sync_api = pytest.importorskip("playwright.sync_api")
    errors = []
    with sync_api.sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        # Le serveur de test ne lance pas le planificateur : son état est simulé.
        page.route("**/api/saved-loops", lambda route: route.fulfill(json={
            "active": True, "running": None,
            "searches": {"Mini-PC home lab": {"last_s": 720, "next_s": 1080, "every": 30}}}))
        page.goto(server)
        page.click(".tab[data-tab=saved]")
        card = page.locator("#saved-list [data-saved]").first
        card.wait_for()
        card.locator('[data-s="loop"]').check(force=True)
        card.locator('[data-s="every"]').fill("45")  # pas encore enregistré
        sync_api.expect(card.locator("[data-loop-status]")).to_contain_text("Enregistrez")
        every = card.locator('[data-s="every"]')
        every.fill("5")
        every.dispatch_event("change")
        sync_api.expect(every).to_have_value("30")
        page.keyboard.press("Control+s")
        page.wait_for_function("document.querySelector('#save-status').innerText.includes('Tout est enregistré')")
        sync_api.expect(card.locator("[data-loop-status]")).to_contain_text(
            "Dernier scan il y a 12 min · prochain dans 18 min")
        browser.close()
    assert errors == []


def test_link_analysis_tab(server):
    """Onglet Analyse : lien collé → suivi animé → bilan affiché ; site qui refuse → saisie manuelle."""
    sync_api = pytest.importorskip("playwright.sync_api")
    from test_link import BILAN

    record = {"id": "1", "at": "2026-11-23T20:00:00+00:00",
              "ad": {"title": "ThinkCentre M920q i5 16 Go", "url": "https://www.vinted.fr/items/1", "source": "vinted",
                     "price": 95, "total_price": 95, "photos": [], "available": True},
              "result": {**BILAN, "arnaque": {"niveau": "faible", "raisons": []}, "prix_achat_max": 96,
                         "marge_visee": 48, "cost_usd": 0.21, "model": "claude-opus-5-5", "photos_analysees": 0}}
    errors, bodies = [], []
    with sync_api.sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        def analysis(route):
            body = route.request.post_data_json
            bodies.append(body)
            if "leboncoin" in body["url"] and not body.get("title"):
                route.fulfill(status=422, json={"ok": False, "manual": True, "error": "leboncoin refuse : à la main"})
            else:
                route.fulfill(json={"ok": True, "item": record})

        page.route("**/api/link-analysis", analysis)
        page.goto(server)
        page.click(".tab[data-tab=analyze]")
        page.fill("#link-url", "https://www.leboncoin.fr/ad/x/1")
        page.click("#link-run")
        sync_api.expect(page.locator("#link-manual")).to_be_visible()
        page.fill("#lm-title", "ThinkCentre M920q")
        page.fill("#lm-price", "95")
        page.click("#link-run")
        report = page.locator("#link-result .link-report")
        sync_api.expect(report).to_contain_text("À acheter")
        sync_api.expect(report).to_contain_text("Points forts")
        sync_api.expect(report.locator(".note-ring")).to_contain_text("8,4")
        sync_api.expect(report).to_contain_text("Le BIOS est-il verrouillé ?")
        browser.close()
    assert errors == []
    assert bodies[-1]["title"] == "ThinkCentre M920q" and bodies[-1]["price"] == "95"
