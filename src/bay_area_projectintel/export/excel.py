from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from bay_area_projectintel.db import Database
from bay_area_projectintel.models import Category


HEADERS = ["状态", "项目描述", "城市", "日期", "投标截止", "公司名", "邮箱", "电话", "首次发现日期", "来源链接"]
NEW_FILL = PatternFill("solid", fgColor="FFF2CC")
DEADLINE_FILL = PatternFill("solid", fgColor="F4CCCC")
HEADER_FILL = PatternFill("solid", fgColor="D9EAD3")


def export_excel(db: Database, out: Path, category: str | None = None) -> dict[str, int]:
    rows = db.export_rows(category=category)
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"

    by_category: dict[str, list[Any]] = defaultdict(list)
    pending: list[Any] = []
    for row in rows:
        has_contact = bool(row["email"] or row["phone"])
        if has_contact:
            by_category[row["category"] or Category.OTHER.value].append(row)
        else:
            pending.append(row)

    _write_summary(summary, rows)
    for category_name in Category:
        data = by_category.get(category_name.value, [])
        if data:
            _write_rows(wb.create_sheet(category_name.value[:31]), data)

    pending_sheet = wb.create_sheet("待补全 (Pending)")
    _write_rows(pending_sheet, pending)

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return {"total": len(rows), "exported": sum(len(v) for v in by_category.values()), "pending": len(pending)}


def _write_summary(sheet: Any, rows: list[Any]) -> None:
    sheet.append(["Category", "County", "Total", "With Contact", "Coverage"])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL

    counts: Counter[tuple[str, str]] = Counter()
    contact_counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        key = (row["category"] or Category.OTHER.value, row["county"] or "")
        counts[key] += 1
        if row["email"] or row["phone"]:
            contact_counts[key] += 1

    for (category, county), total in sorted(counts.items()):
        with_contact = contact_counts[(category, county)]
        coverage = with_contact / total if total else 0
        sheet.append([category, county, total, with_contact, coverage])
        sheet.cell(sheet.max_row, 5).number_format = "0.0%"
    _autosize(sheet)


def _write_rows(sheet: Any, rows: list[Any]) -> None:
    sheet.append(HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL

    today = date.today().isoformat()
    deadline_col = HEADERS.index("投标截止") + 1
    for row in rows:
        status = "🆕新增" if str(row["first_seen"]).startswith(today) else "已有"
        bid_deadline = row["bid_deadline"]
        sheet.append(
            [
                status,
                row["description"],
                row["city"],
                row["project_date"],
                bid_deadline,
                row["company_name"],
                row["email"],
                row["phone"],
                str(row["first_seen"]).split("T", 1)[0],
                row["source_url"],
            ]
        )
        excel_row = sheet.max_row
        if status.startswith("🆕"):
            for cell in sheet[excel_row]:
                cell.fill = NEW_FILL
        if bid_deadline:
            sheet.cell(excel_row, deadline_col).fill = DEADLINE_FILL
        link_cell = sheet.cell(excel_row, len(HEADERS))
        if link_cell.value:
            link_cell.hyperlink = link_cell.value
            link_cell.style = "Hyperlink"

    sheet.freeze_panes = "A2"
    _autosize(sheet)


def _autosize(sheet: Any) -> None:
    for column_cells in sheet.columns:
        max_length = 0
        column = get_column_letter(column_cells[0].column)
        for cell in column_cells:
            value = "" if cell.value is None else str(cell.value)
            max_length = max(max_length, min(len(value), 80))
        sheet.column_dimensions[column].width = max(12, min(max_length + 2, 60))
