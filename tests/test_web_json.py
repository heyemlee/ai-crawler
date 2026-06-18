from bay_area_projectintel.export.web_json import build_web_data


def _row(**kw):
    base = {
        "company_name": "", "category": "OTHER", "city": "", "county": "", "address": "",
        "description": "", "email": None, "phone": None, "source": "s", "project_date": "",
        "first_seen": "", "source_url": None, "license_number": None,
    }
    base.update(kw)
    return base


def test_build_web_data_flags_new_and_counts():
    rows = [
        _row(company_name="A", category="COMMERCIAL_TI", city="SF", email="a@x.com",
             project_date="2026-06-01", first_seen="2026-06-15T00:00:00"),
        _row(company_name="B", category="OTHER", first_seen="2026-01-01T00:00:00"),
    ]
    d = build_web_data(rows, today="2026-06-18", new_window_days=7)

    assert d["total"] == 2
    assert d["with_contact"] == 1
    assert d["new_count"] == 1  # only A's first_seen is >= 2026-06-11
    cats = {c["key"]: c for c in d["categories"]}
    assert cats["COMMERCIAL_TI"]["new"] == 1
    assert cats["OTHER"]["new"] == 0
    assert d["cities"] == ["SF"]
    # New rows sort to the top.
    assert d["leads"][0]["company"] == "A"
    assert d["leads"][0]["is_new"] is True
    assert d["leads"][1]["is_new"] is False


def test_description_is_capped():
    d = build_web_data([_row(description="x" * 500)], today="2026-06-18")
    assert len(d["leads"][0]["desc"]) <= 301 and d["leads"][0]["desc"].endswith("…")
