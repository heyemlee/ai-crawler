from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from bay_area_projectintel.compliance.politeness import PoliteHttpClient
from bay_area_projectintel.config import SourceConfig
from bay_area_projectintel.db import stable_hash
from bay_area_projectintel.models import RawRecord


class SocrataPermitsSource:
    """Generic Socrata building-permits adapter, driven by SourceConfig.field_map.

    Adding a Socrata city is config-only: set domain/dataset_id/date_field and a
    field_map. The optional contacts_dataset_id join is DataSF-specific (keyed by
    permit number); cities without it carry the contractor on the permit record.
    """

    def __init__(self, source_name: str, config: SourceConfig, client: PoliteHttpClient):
        self.name = source_name
        self.config = config
        self.client = client
        self.latest_watermark: str | None = None

    def fetch(self, since: str | None = None, limit: int | None = None) -> Iterable[RawRecord]:
        fetched = 0
        offset = 0
        max_seen: str | None = None
        page_size = min(self.config.page_size, limit or self.config.page_size)
        base_url = f"https://{self.config.domain}/resource/{self.config.dataset_id}.json"
        id_field = self.config.field_map.record_id
        permit_field = self.config.field_map.permit_number

        while True:
            params: dict[str, object] = {
                "$limit": page_size,
                "$offset": offset,
                "$order": f"{self.config.date_field} ASC",
            }
            if since:
                params["$where"] = f"{self.config.date_field} > '{_socrata_timestamp(since)}'"

            page = self.client.get_json(base_url, params=params)
            if not isinstance(page, list):
                raise ValueError(f"Unexpected Socrata response for {self.name}: {page!r}")
            if not page:
                break

            permit_numbers = [str(row.get(permit_field, "")) for row in page] if permit_field else []
            contacts_by_permit = self._fetch_contacts(permit_numbers)
            for payload in page:
                permit_number = str(payload.get(permit_field) or "") if permit_field else ""
                if permit_number and contacts_by_permit.get(permit_number):
                    payload["_contacts"] = contacts_by_permit[permit_number]
                payload["_source_url"] = self.source_url(payload)
                source_record_id = str(payload.get(id_field) or permit_number or stable_hash(payload))
                content_hash = stable_hash(payload)
                yield RawRecord(
                    source=self.name,
                    source_record_id=source_record_id,
                    payload=payload,
                    content_hash=content_hash,
                )
                fetched += 1
                date_value = payload.get(self.config.date_field)
                if isinstance(date_value, str) and (max_seen is None or date_value > max_seen):
                    max_seen = date_value
                if limit and fetched >= limit:
                    self.latest_watermark = max_seen
                    return

            offset += len(page)
            if len(page) < page_size:
                break

        self.latest_watermark = max_seen

    def source_url(self, payload: dict[str, Any]) -> str | None:
        if not self.config.source_url_template:
            return None
        try:
            return self.config.source_url_template.format(**payload)
        except (KeyError, IndexError):
            return None

    def _fetch_contacts(self, permit_numbers: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not self.config.contacts_dataset_id:
            return {}
        clean = sorted({number for number in permit_numbers if number})
        if not clean:
            return {}
        url = f"https://{self.config.domain}/resource/{self.config.contacts_dataset_id}.json"
        results: dict[str, list[dict[str, Any]]] = {}
        for chunk in _chunks(clean, 40):
            quoted = ",".join(f"'{number.replace(chr(39), chr(39) + chr(39))}'" for number in chunk)
            params: dict[str, object] = {
                "$limit": 5000,
                "$where": f"permit_number in({quoted})",
            }
            contacts = self.client.get_json(url, params=params)
            if not isinstance(contacts, list):
                continue
            for contact in contacts:
                permit_number = str(contact.get("permit_number") or "")
                results.setdefault(permit_number, []).append(contact)
        return results


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _socrata_timestamp(value: str) -> str:
    if "T" in value:
        return value
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return value
