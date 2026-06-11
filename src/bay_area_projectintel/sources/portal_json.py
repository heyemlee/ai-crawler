from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from datetime import datetime
from typing import Any

from bay_area_projectintel.compliance.politeness import PoliteHttpClient
from bay_area_projectintel.config import SourceConfig
from bay_area_projectintel.db import stable_hash
from bay_area_projectintel.models import RawRecord


class JsonPermitPortalSource:
    """Config-driven adapter for permit portals with public JSON search endpoints.

    Accela and EnerGov deployments vary by city: URLs, response envelopes, and field
    names are not consistent. This adapter keeps those differences in sources.yaml
    and emits RawRecord payloads that reuse the existing Socrata normalizer.
    """

    source_kind = "permit_portal"

    def __init__(self, source_name: str, config: SourceConfig, client: PoliteHttpClient):
        if not config.search_url:
            raise ValueError(f"{source_name}: search_url is required for {self.source_kind}")
        self.name = source_name
        self.config = config
        self.client = client
        self.latest_watermark: str | None = None

    def fetch(self, since: str | None = None, limit: int | None = None) -> Iterable[RawRecord]:
        fetched = 0
        offset = 0
        page = 1
        max_seen: str | None = None
        page_size = min(self.config.page_size, limit or self.config.page_size)

        while True:
            params = dict(self.config.query_params)
            if self.config.since_param and since:
                params[self.config.since_param] = since
            if self.config.limit_param:
                params[self.config.limit_param] = page_size
            if self.config.offset_param:
                params[self.config.offset_param] = offset
            if self.config.page_param:
                params[self.config.page_param] = page

            response = self.client.get_json(self.config.search_url, params=params, check_robots=False)
            records = _extract_records(response, self.config.record_path)
            if not records:
                break

            for payload in records:
                payload = dict(payload)
                if since and not self.config.since_param and not _is_recent(payload, self.config, since):
                    continue
                payload["_source_url"] = self.source_url(payload)
                source_record_id = self.source_record_id(payload)
                yield RawRecord(
                    source=self.name,
                    source_record_id=source_record_id,
                    payload=payload,
                    content_hash=stable_hash(payload),
                )
                fetched += 1

                date_value = _first_present(payload, [self.config.date_field, *self.config.field_map.project_date])
                if isinstance(date_value, str) and _is_later(date_value, max_seen):
                    max_seen = date_value
                if limit and fetched >= limit:
                    self.latest_watermark = max_seen
                    return

            if len(records) < page_size:
                break
            offset += len(records)
            page += 1

        self.latest_watermark = max_seen

    def source_record_id(self, payload: dict[str, Any]) -> str:
        field_map = self.config.field_map
        value = payload.get(field_map.record_id)
        if value in (None, "") and field_map.permit_number:
            value = payload.get(field_map.permit_number)
        return str(value or stable_hash(payload))

    def source_url(self, payload: dict[str, Any]) -> str | None:
        template = self.config.source_url_template or self.config.detail_url_template
        if not template:
            return None
        try:
            return template.format(**payload)
        except (KeyError, IndexError):
            return None


class AccelaPermitsSource(JsonPermitPortalSource):
    source_kind = "accela_building_permits"


class EnerGovPermitsSource(JsonPermitPortalSource):
    source_kind = "energov_building_permits"

    def fetch(self, since: str | None = None, limit: int | None = None) -> Iterable[RawRecord]:
        if not self.config.criteria_url:
            yield from super().fetch(since=since, limit=limit)
            return

        criteria_response = self.client.get_json(
            self.config.criteria_url,
            headers=self.config.request_headers,
            check_robots=False,
        )
        criteria = _path_get(criteria_response, self.config.record_path or "Result") or _path_get(criteria_response, "Result")
        if not isinstance(criteria, dict):
            raise ValueError(f"{self.name}: could not load EnerGov search criteria template")

        fetched = 0
        page = 1
        max_seen: str | None = None
        page_size = min(self.config.page_size, limit or self.config.page_size)

        while True:
            payload = _energov_search_payload(criteria, since=since, page=page, page_size=page_size)
            response = self.client.post_json(
                self.config.search_url,
                payload,
                headers=self.config.request_headers,
                check_robots=False,
            )
            records = _extract_records(response, self.config.record_path or "Result.EntityResults")
            if not records:
                break

            for payload in records:
                payload = self._with_detail(dict(payload))
                payload["_source_url"] = self.source_url(payload)
                source_record_id = self.source_record_id(payload)
                yield RawRecord(
                    source=self.name,
                    source_record_id=source_record_id,
                    payload=payload,
                    content_hash=stable_hash(payload),
                )
                fetched += 1

                date_value = _first_present(payload, [self.config.date_field, *self.config.field_map.project_date])
                if isinstance(date_value, str) and _is_later(date_value, max_seen):
                    max_seen = date_value
                if limit and fetched >= limit:
                    self.latest_watermark = max_seen
                    return

            if len(records) < page_size:
                break
            page += 1

        self.latest_watermark = max_seen

    def _with_detail(self, payload: dict[str, Any]) -> dict[str, Any]:
        case_id = payload.get("CaseId") or payload.get("PermitId")
        if not case_id or not self.config.detail_url_template:
            return payload
        try:
            detail_url = self.config.detail_url_template.format(**payload)
        except (KeyError, IndexError):
            return payload
        try:
            detail = self.client.get_json(detail_url, headers=self.config.request_headers, check_robots=False)
        except Exception:
            return payload
        result = _path_get(detail, "Result")
        if isinstance(result, dict):
            payload.update({f"detail_{key}": value for key, value in result.items()})
        return payload


def _extract_records(response: object, record_path: str | None) -> list[dict[str, Any]]:
    data = _path_get(response, record_path) if record_path else response
    if isinstance(data, list):
        return [dict(row) for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("records", "result", "results", "data", "items", "rows", "Rows"):
            value = data.get(key)
            if isinstance(value, list):
                return [dict(row) for row in value if isinstance(row, dict)]
    return []


def _path_get(value: object, path: str | None) -> object:
    if not path:
        return value
    current: object = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current


def _first_present(payload: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _is_recent(payload: dict[str, Any], config: SourceConfig, since: str) -> bool:
    value = _first_present(payload, [config.date_field, *config.field_map.project_date])
    if not value:
        return True
    record_date = _parse_date(value)
    since_date = _parse_date(since)
    if record_date is None or since_date is None:
        return True
    return record_date >= since_date


def _is_later(value: str, current: str | None) -> bool:
    if current is None:
        return True
    value_date = _parse_date(value)
    current_date = _parse_date(current)
    if value_date is not None and current_date is not None:
        return value_date > current_date
    return value > current


def _parse_date(value: str | None):
    if not value:
        return None
    text = str(value).strip().split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _energov_search_payload(criteria: dict[str, Any], *, since: str | None, page: int, page_size: int) -> dict[str, Any]:
    payload = deepcopy(criteria)
    payload["SearchModule"] = 2  # Permit
    payload["FilterModule"] = 0
    payload["Keyword"] = ""
    payload["ExactMatch"] = True
    payload["PageNumber"] = page
    payload["PageSize"] = page_size
    payload["SortBy"] = "PermitNumber"
    payload["SortAscending"] = False

    permit = payload.setdefault("PermitCriteria", {})
    permit["PageNumber"] = page
    permit["PageSize"] = page_size
    permit["SortBy"] = "PermitNumber"
    permit["SortAscending"] = False
    permit["PermitTypeId"] = permit.get("PermitTypeId") or "none"
    permit["PermitStatusId"] = permit.get("PermitStatusId") or "none"
    if since:
        permit["ApplyDateFrom"] = _us_date(since)
    return payload


def _us_date(value: str) -> str:
    text = value.split("T", 1)[0]
    try:
        return datetime.fromisoformat(text).strftime("%m/%d/%Y")
    except ValueError:
        return value
