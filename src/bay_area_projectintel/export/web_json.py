"""Export leads as a compact JSON for the static viewer page (Cloudflare/Vercel).

The viewer is a plain static site that reads this one file, so all the shaping —
contact coverage, per-category counts, and the "newly crawled" flag — happens here.
``is_new`` marks rows whose ``first_seen`` falls within the recent window (default 7
days), so the page can badge and filter the latest crawl.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

_DESC_CAP = 300


def _val(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def build_web_data(rows: Iterable[Any], *, today: str | None = None, new_window_days: int = 7) -> dict[str, Any]:
    today = today or date.today().isoformat()
    new_since = (date.fromisoformat(today) - timedelta(days=new_window_days)).isoformat()

    leads: list[dict[str, Any]] = []
    cat_counts: dict[str, dict[str, int]] = {}
    cities: set[str] = set()
    with_contact = 0
    new_count = 0

    for row in rows:
        first_seen = (str(_val(row, "first_seen") or ""))[:10]
        is_new = bool(first_seen) and first_seen >= new_since
        email = _val(row, "email") or ""
        phone = _val(row, "phone") or ""
        if email or phone:
            with_contact += 1
        if is_new:
            new_count += 1

        category = _val(row, "category") or "OTHER"
        bucket = cat_counts.setdefault(category, {"count": 0, "new": 0})
        bucket["count"] += 1
        if is_new:
            bucket["new"] += 1

        city = _val(row, "city") or ""
        if city:
            cities.add(city)

        desc = str(_val(row, "description") or "")
        if len(desc) > _DESC_CAP:
            desc = desc[:_DESC_CAP] + "…"

        leads.append(
            {
                "company": _val(row, "company_name") or "",
                "category": category,
                "city": city,
                "county": _val(row, "county") or "",
                "address": _val(row, "address") or "",
                "desc": desc,
                "email": email,
                "phone": phone,
                "source": _val(row, "source") or "",
                "date": _val(row, "project_date") or "",
                "first_seen": first_seen,
                "url": _val(row, "source_url") or "",
                "license": _val(row, "license_number") or "",
                "is_new": is_new,
            }
        )

    # New first, then most recent project date — so the latest crawl floats to the top.
    leads.sort(key=lambda x: (x["is_new"], x["date"], x["first_seen"]), reverse=True)

    categories = [
        {"key": key, "count": val["count"], "new": val["new"]}
        for key, val in sorted(cat_counts.items())
    ]
    return {
        "generated_at": today,
        "new_since": new_since,
        "new_window_days": new_window_days,
        "total": len(leads),
        "with_contact": with_contact,
        "new_count": new_count,
        "categories": categories,
        "cities": sorted(cities),
        "leads": leads,
    }


def export_web_json(db, out_path: Path, *, today: str | None = None, new_window_days: int = 7) -> dict[str, Any]:
    data = build_web_data(db.export_rows(), today=today, new_window_days=new_window_days)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {
        "total": data["total"],
        "with_contact": data["with_contact"],
        "new_count": data["new_count"],
        "out": str(out_path),
    }
