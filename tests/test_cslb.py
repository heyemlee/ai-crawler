from bay_area_projectintel.enrichment.cslb import CslbEnricher, clean_phone, normalize_license


def test_normalize_license_keeps_numeric_contractor_license() -> None:
    assert normalize_license("1122753") == "1122753"
    assert normalize_license("C-86522") is None
    assert normalize_license(None) is None


def test_cslb_csv_lookup_updates_phone_and_license_info(tmp_path) -> None:
    csv_path = tmp_path / "MasterLicenseData.csv"
    csv_path.write_text(
        "LicenseNo,FullBusinessName,BusinessPhone,MailingAddress,City,State,ZIPCode,PrimaryStatus,SecondaryStatus,Classifications(s)\n"
        "492944,BCCI CONSTRUCTION COMPANY,4155551212,555 Main St,San Francisco,CA,94105,CLEAR,,B\n",
        encoding="utf-8",
    )

    enricher = CslbEnricher(csv_path, target_licenses=["492944"])
    result = enricher.enrich("Bcci Construction Company", "492944")

    assert result.status == "updated"
    assert result.phone == "(415) 555-1212"
    assert result.address == "555 Main St San Francisco CA 94105"
    assert result.license_status == "CLEAR"
    assert result.license_classification == "B"


def test_cslb_match_without_phone_still_returns_license_info(tmp_path) -> None:
    csv_path = tmp_path / "MasterLicenseData.csv"
    csv_path.write_text(
        "LicenseNo,FullBusinessName,BusinessPhone,MailingAddress,City,State,ZIPCode,PrimaryStatus,Classifications(s)\n"
        "492944,BCCI CONSTRUCTION COMPANY,,555 Main St,San Francisco,CA,94105,CLEAR,B\n",
        encoding="utf-8",
    )

    result = CslbEnricher(csv_path).enrich("Bcci", "492944")

    assert result.status == "not_found"
    assert result.phone is None
    assert result.address == "555 Main St San Francisco CA 94105"
    assert result.license_status == "CLEAR"


def test_clean_phone_leaves_unexpected_values_readable() -> None:
    assert clean_phone("415.555.1212") == "(415) 555-1212"
    assert clean_phone("N/A") == "N/A"
