"""Client HTTP asynchrone partagé.

Délai minimum par site, robots.txt sur demande, nouvel essai sur 429/5xx,
et `AccessDenied` immédiat sur 401/403 (le site est alors mis en pause).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 15 * 60


class AccessDenied(RuntimeError):
    """Le site refuse l'accès (401/403) ou robots.txt l'interdit : on n'insiste pas."""


class PoliteClient:
    def __init__(
        self,
        user_agent: str,
        min_host_interval: float = 10.0,
        max_retries: int = 4,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
        host_intervals: dict[str, float] | None = None,
    ):
        self.user_agent = user_agent
        self.min_host_interval = min_host_interval
        self.host_intervals = {h.lower(): float(v) for h, v in (host_intervals or {}).items()}  # exceptions par site
        self.max_retries = max_retries
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, transport=transport,
            headers={"User-Agent": user_agent, "Accept-Language": "fr-FR,fr;q=0.9"},
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),  # léger pour un Raspberry Pi
        )
        self._last_request: dict[str, float] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _wait_turn(self, host: str) -> None:
        """Espace les requêtes vers un même site (avec un peu d'aléa pour étaler la charge)."""
        interval = self.host_intervals.get(host.lower(), self.min_host_interval)
        elapsed = time.monotonic() - self._last_request.get(host, 0.0)
        wait = interval - elapsed
        if wait > 0:
            await self._sleep(wait + random.uniform(0, interval * 0.2))
        self._last_request[host] = time.monotonic()

    async def allowed_by_robots(self, url: str) -> bool:
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                resp = await self._client.get(f"{base}/robots.txt")
                if resp.status_code >= 400:
                    # Pas de robots.txt (404) : rien d'interdit ; accès refusé (401/403) : on s'abstient.
                    parser = None if resp.status_code in (401, 403) else _allow_all()
                else:
                    parser.parse(resp.text.splitlines())
            except httpx.HTTPError:
                parser = None
            self._robots[base] = parser
        parser = self._robots[base]
        return parser is not None and parser.can_fetch(self.user_agent, url)

    async def request(self, method: str, url: str, *, check_robots: bool = False, **kwargs) -> httpx.Response:
        if check_robots and not await self.allowed_by_robots(url):
            raise AccessDenied(f"robots.txt n'autorise pas {url}")
        host = urlparse(url).netloc
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        for attempt in range(self.max_retries + 1):
            async with lock:
                await self._wait_turn(host)
                try:
                    resp = await self._client.request(method, url, **kwargs)
                except httpx.TransportError as exc:
                    resp, error = None, exc
            if resp is not None:
                if resp.status_code in (401, 403):
                    raise AccessDenied(f"HTTP {resp.status_code} sur {host}")
                if resp.status_code != 429 and resp.status_code < 500:
                    return resp
                error = httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
            if attempt == self.max_retries:
                raise error
            delay = _retry_after(resp) or min(MAX_BACKOFF_SECONDS, 30 * 2**attempt) * random.uniform(0.8, 1.2)
            log.warning("%s : %s, nouvelle tentative dans %.0f s (%d/%d)", host, error, delay, attempt + 1,
                        self.max_retries)
            await self._sleep(delay)
        raise RuntimeError("inatteignable")

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)


def _allow_all() -> RobotFileParser:
    parser = RobotFileParser()
    parser.parse([])
    return parser


def _retry_after(resp: httpx.Response | None) -> float | None:
    if resp is None:
        return None
    try:
        return min(float(resp.headers.get("Retry-After", "")), MAX_BACKOFF_SECONDS)
    except ValueError:
        return None
