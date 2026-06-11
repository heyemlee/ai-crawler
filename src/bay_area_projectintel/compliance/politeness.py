from __future__ import annotations

import hashlib
import time
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

import httpx


class PoliteHttpClient:
    def __init__(self, cache_dir: Path, user_agent: str, min_interval_seconds: float = 0.35):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent
        self.min_interval_seconds = min_interval_seconds
        self._last_request_by_domain: dict[str, float] = {}
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self._client = httpx.Client(timeout=30, headers={"User-Agent": user_agent})

    def close(self) -> None:
        self._client.close()

    def get_json(
        self,
        url: str,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
        check_robots: bool = True,
    ) -> object:
        if check_robots:
            self.ensure_allowed(url)
        cache_path = self._cache_path(url, params)
        if use_cache and cache_path.exists():
            return httpx.Response(200, content=cache_path.read_bytes()).json()

        self.throttle(url)
        response = self._client.get(url, params=params, headers=headers)
        response.raise_for_status()
        cache_path.write_bytes(response.content)
        return response.json()

    def post_json(
        self,
        url: str,
        payload: dict[str, object] | None = None,
        *,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
        check_robots: bool = True,
    ) -> object:
        if check_robots:
            self.ensure_allowed(url)
        cache_path = self._cache_path(url, {"__method": "POST", "__payload": repr(payload or {})})
        if use_cache and cache_path.exists():
            return httpx.Response(200, content=cache_path.read_bytes()).json()

        self.throttle(url)
        response = self._client.post(url, json=payload or {}, headers=headers)
        response.raise_for_status()
        cache_path.write_bytes(response.content)
        return response.json()

    def get_text(self, url: str, use_cache: bool = True) -> str:
        self.ensure_allowed(url)
        cache_path = self._cache_path(url, None)
        if use_cache and cache_path.exists():
            return cache_path.read_text(encoding="utf-8", errors="replace")

        self.throttle(url)
        response = self._client.get(url)
        response.raise_for_status()
        cache_path.write_bytes(response.content)
        return response.text

    def ensure_allowed(self, url: str) -> None:
        parsed = urlparse(url)
        domain = parsed.netloc
        robots = self._robots.get(domain)
        if robots is None:
            robots = robotparser.RobotFileParser()
            robots.set_url(f"{parsed.scheme}://{domain}/robots.txt")
            try:
                robots.read()
            except Exception:
                # If robots cannot be read, stay conservative but do not block official APIs.
                pass
            self._robots[domain] = robots
        if robots.default_entry is not None and not robots.can_fetch(self.user_agent, url):
            raise PermissionError(f"robots.txt disallows fetching {url}")

    def throttle(self, url: str) -> None:
        domain = urlparse(url).netloc
        now = time.monotonic()
        last = self._last_request_by_domain.get(domain, 0)
        wait = self.min_interval_seconds - (now - last)
        if wait > 0:
            time.sleep(wait)
        self._last_request_by_domain[domain] = time.monotonic()

    def _cache_path(self, url: str, params: dict[str, object] | None) -> Path:
        key = repr((url, sorted((params or {}).items()))).encode("utf-8")
        digest = hashlib.sha256(key).hexdigest()
        return self.cache_dir / f"{digest}.json"
