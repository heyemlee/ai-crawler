from bay_area_projectintel.report import build_report


ROWS = [
    {
        "category": "RESTAURANT_RETAIL",
        "email": "info@acme.com",
        "phone": None,
        "first_seen": "2026-05-28T10:00:00",
        "license_number": "492944",
        "source": "datasf-building-permits",
    },
    {
        "category": "PUBLIC_WORKS",
        "email": "jane@gsa.gov",
        "phone": "(415) 555-9876",
        "first_seen": "2026-05-15T09:00:00",
        "license_number": None,
        "source": "samgov-construction",
    },
    {
        "category": "OFFICE_LAB",
        "email": None,
        "phone": None,
        "first_seen": "2026-05-15T09:00:00",
        "license_number": None,
        "source": "datasf-building-permits",
    },
]


def test_build_report_aggregates_counts_and_coverage() -> None:
    summary = build_report(ROWS, today="2026-05-28")

    assert summary.total == 3
    assert summary.with_contact == 2
    assert summary.pending == 1
    assert summary.new_today == 1
    assert summary.rfp_leads == 1
    # High value: license-backed permit lead + RFP POC lead.
    assert summary.high_value == 2
    assert round(summary.coverage, 4) == round(2 / 3, 4)


def test_build_report_per_category_breakdown() -> None:
    summary = build_report(ROWS, today="2026-05-28")
    by_name = {stat.category: stat for stat in summary.by_category}

    assert by_name["OFFICE_LAB"].total == 1
    assert by_name["OFFICE_LAB"].with_contact == 0
    assert by_name["RESTAURANT_RETAIL"].coverage == 1.0
