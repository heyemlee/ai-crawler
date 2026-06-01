from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from bay_area_projectintel.models import Category


@dataclass
class CategoryStat:
    category: str
    total: int = 0
    with_contact: int = 0

    @property
    def coverage(self) -> float:
        return self.with_contact / self.total if self.total else 0.0


@dataclass
class ReportSummary:
    total: int = 0
    with_contact: int = 0
    pending: int = 0
    new_today: int = 0
    high_value: int = 0
    rfp_leads: int = 0
    by_category: list[CategoryStat] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.with_contact / self.total if self.total else 0.0


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def build_report(rows: Iterable[Any], today: str | None = None) -> ReportSummary:
    today = today or date.today().isoformat()
    summary = ReportSummary()
    by_category: dict[str, CategoryStat] = {}

    for row in rows:
        summary.total += 1
        has_contact = bool(_row_value(row, "email") or _row_value(row, "phone"))
        category = _row_value(row, "category") or Category.OTHER.value
        stat = by_category.setdefault(category, CategoryStat(category=category))
        stat.total += 1

        if has_contact:
            summary.with_contact += 1
            stat.with_contact += 1
        else:
            summary.pending += 1

        if str(_row_value(row, "first_seen") or "").startswith(today):
            summary.new_today += 1

        source = str(_row_value(row, "source") or "").lower()
        if "sam" in source:
            summary.rfp_leads += 1

        # Highest-value leads: a reachable contact plus a CSLB license (path B) or an RFP POC (path A).
        if has_contact and (_row_value(row, "license_number") or "sam" in source):
            summary.high_value += 1

    summary.by_category = [by_category[name] for name in sorted(by_category)]
    return summary
