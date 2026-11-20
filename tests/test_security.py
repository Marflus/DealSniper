"""Interface locale : CSRF, rebinding DNS, jeton pour les autres appareils du réseau."""

from dealsniper.config import data_dir
from dealsniper.web.security import COOKIE, host_allowed, ui_token

LAN = {"REMOTE_ADDR": "192.168.1.20"}


def test_cross_site_requests_are_refused(client):
    c, _ = client
    # Une page d'un autre site tente de réécrire les clés ou de lancer une analyse payante.
    evil = {"Origin": "https://pirate.example"}
    assert c.post("/api/secrets", json={"DISCORD_WEBHOOK_URL": "https://x"}, headers=evil).status_code == 403
    assert c.post("/api/run", headers={"Referer": "https://pirate.example/page"}).status_code == 403
    assert c.post("/api/run", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/api/run", headers={"Origin": "null"}).status_code == 403
    # Corps « text/plain » (requête simple, sans pré-vérification CORS) : ignoré, rien n'est écrit.
    resp = c.post("/api/secrets", data='{"DISCORD_WEBHOOK_URL": "https://x"}', content_type="text/plain")
    assert resp.get_json()["updated"] == []
    # L'interface elle-même : acceptée.
    same = {"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"}
    assert c.post("/api/ads/ebay:100000000001/mark", json={"favorite": True}, headers=same).status_code == 200


def test_dns_rebinding_host_is_refused(client):
    c, _ = client
    assert c.get("/api/ads", base_url="http://piege.example.com").status_code == 403
    assert host_allowed("localhost:5000") and host_allowed("192.168.1.10:5000") and host_allowed("[::1]:5000")
    assert not host_allowed("piege.example.com:5000")


def test_lan_devices_need_the_token(client):
    c, _ = client
    token = ui_token(data_dir())
    assert ui_token(data_dir()) == token  # stable
    assert c.get("/api/ads", environ_base=LAN).status_code == 401
    assert c.get("/api/ads?token=mauvais", environ_base=LAN).status_code == 401
    first = c.get(f"/?token={token}", environ_base=LAN)
    assert first.status_code == 200 and COOKIE in first.headers.get("Set-Cookie", "")
    assert c.get("/api/ads", environ_base=LAN).status_code == 200  # cookie gardé ensuite
    assert c.get("/api/ads").status_code == 200  # ce PC : jamais de jeton
