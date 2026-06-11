from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

import httpx

from bay_area_projectintel.compliance.politeness import PoliteHttpClient
from bay_area_projectintel.config import SourceConfig
from bay_area_projectintel.db import stable_hash
from bay_area_projectintel.geo import is_bay_area, is_san_jose_50mi
from bay_area_projectintel.models import RawRecord


STATE_IN_ADDRESS = re.compile(r",\s*([A-Z]{2})\s+\d{5}")


class SamGovOpportunitiesSource:
    """SAM.gov Get Opportunities v2 API (path A: RFPs carry an issuing-agency POC).

    Requires a free SAM_API_KEY. The API is official, so robots.txt is not
    consulted (API-first); per-domain rate limiting and the local cache still
    apply via the polite client.
    """

    def __init__(
        self,
        source_name: str,
        config: SourceConfig,
        client: PoliteHttpClient,
        api_key: str | None = None,
    ):
        self.name = source_name
        self.config = config
        self.client = client
        self.api_key = api_key
        self.latest_watermark: str | None = None
        self.rate_limited = False

    def fetch(self, since: str | None = None, limit: int | None = None) -> Iterable[RawRecord]:
        if not self.api_key:
            raise RuntimeError(
                "SAM_API_KEY is required for SAM.gov. Register for a free key at sam.gov "
                "and set SAM_API_KEY in your .env."
            )

        posted_from = _sam_date(since) if since else _sam_date(date.today().isoformat())
        posted_to = _sam_date(date.today().isoformat())
        codes: list[str | None] = list(self.config.naics_codes) or [None]
        region = self.config.region

        # SAM has no ascending-sort param, so a watermark would skip older un-fetched
        # records. Instead each run re-pulls the lookback window and relies on DB
        # dedup; latest_watermark stays None so the CLI keeps using the window.
        fetched = 0
        for ncode in codes:
            if self.rate_limited:
                break
            for payload in self._fetch_for_code(ncode, posted_from, posted_to):
                # The SAM API geo params are unreliable, so filter on the actual
                # place-of-performance client-side (Bay Area focus).
                if region and not _region_matches(region, payload):
                    continue

                payload["_contacts"] = payload.get("pointOfContact") or []
                payload["_source_url"] = payload.get("uiLink") or self.source_url(payload)
                source_record_id = str(payload.get("noticeId") or stable_hash(payload))
                yield RawRecord(
                    source=self.name,
                    source_record_id=source_record_id,
                    payload=payload,
                    content_hash=stable_hash(payload),
                )
                fetched += 1
                if limit and fetched >= limit:
                    return

    def _fetch_for_code(
        self,
        ncode: str | None,
        posted_from: str,
        posted_to: str,
    ) -> Iterable[dict[str, Any]]:
        page_size = self.config.page_size
        offset = 0
        for _ in range(max(1, self.config.max_pages)):
            params: dict[str, object] = {
                "api_key": self.api_key,
                "postedFrom": posted_from,
                "postedTo": posted_to,
                "limit": page_size,
                "offset": offset,
            }
            if ncode:
                params["ncode"] = ncode
            if self.config.ptype:
                params["ptype"] = self.config.ptype

            try:
                response = self.client.get_json(self.config.api_base_url, params=params, check_robots=False)
            except httpx.HTTPStatusError as exc:
                # The free SAM key is rate limited; stop paginating and keep what we have.
                if exc.response is not None and exc.response.status_code == 429:
                    self.rate_limited = True
                    break
                raise
            if not isinstance(response, dict):
                raise ValueError(f"Unexpected SAM.gov response for {self.name}: {response!r}")
            page = response.get("opportunitiesData") or []
            if not page:
                break

            yield from page

            offset += len(page)
            if len(page) < page_size:
                break

    def source_url(self, payload: dict[str, Any]) -> str | None:
        if not self.config.source_url_template:
            return None
        try:
            return self.config.source_url_template.format(**payload)
        except (KeyError, IndexError):
            return None


def _region_matches(region: str, payload: dict[str, Any]) -> bool:
    normalized_region = region.lower().replace("-", "_")
    if normalized_region == "bay_area":
        city, zip_code = _place_city_zip(payload)
        return is_bay_area(city, zip_code, _place_state(payload))
    if normalized_region in {"san_jose_50mi", "sanjose_50mi"}:
        city, zip_code = _place_city_zip(payload)
        return is_san_jose_50mi(city, zip_code, _place_state(payload))
    if len(region) == 2:
        return _place_state(payload) == region.upper()
    return True


def _place_city_zip(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    place = payload.get("placeOfPerformance")
    if not isinstance(place, dict):
        return None, None
    city = (place.get("city") or {}).get("name") if isinstance(place.get("city"), dict) else None
    zip_code = place.get("zip")
    if not zip_code:
        for key in ("streetAddress", "streetAddress2"):
            addr = place.get(key)
            if isinstance(addr, str):
                match = re.search(r"\b(\d{5})\b", addr)
                if match:
                    zip_code = match.group(1)
                    break
    return city, zip_code


def _place_state(payload: dict[str, Any]) -> str | None:
    """Resolve the place-of-performance state code, falling back to the street address."""
    place = payload.get("placeOfPerformance")
    if not isinstance(place, dict):
        return None
    state = place.get("state")
    if isinstance(state, dict) and state.get("code"):
        return str(state["code"]).upper()
    for key in ("streetAddress", "streetAddress2"):
        addr = place.get(key)
        if isinstance(addr, str):
            match = STATE_IN_ADDRESS.search(addr)
            if match:
                return match.group(1).upper()
    return None


def _sam_date(value: str) -> str:
    """Convert an ISO date/timestamp to the MM/dd/yyyy format SAM.gov expects."""
    head = value.split("T", 1)[0]
    try:
        parsed = datetime.strptime(head, "%Y-%m-%d").date()
    except ValueError:
        return value
    return parsed.strftime("%m/%d/%Y")
