from bay_area_projectintel.pipeline.normalize import _company_from_contacts


def test_skips_individual_owner_with_no_firm_or_license() -> None:
    contacts = [
        {"role": "Owner", "first_name": "Jane", "last_name": "Doe"},
        {"role": "Contractor", "firm_name": "Acme Builders Inc", "license1": "492944"},
    ]

    company = _company_from_contacts(contacts)

    assert company is not None
    assert company.name == "Acme Builders Inc"
    assert company.license_number == "492944"


def test_owner_with_firm_name_is_kept_when_no_contractor() -> None:
    contacts = [{"role": "Owner", "firm_name": "Riverside Development LLC", "phone": "4155551212"}]

    company = _company_from_contacts(contacts)

    assert company is not None
    assert company.name == "Riverside Development LLC"
    assert company.phone == "(415) 555-1212"


def test_returns_none_when_only_individual_owner() -> None:
    contacts = [{"role": "Owner", "first_name": "Jane", "last_name": "Doe"}]

    assert _company_from_contacts(contacts) is None


def test_prefers_firm_entity_over_individual_contractor() -> None:
    contacts = [
        {"role": "Contractor", "first_name": "Bob", "last_name": "Smith"},
        {"role": "Contractor", "firm_name": "BuildRight Co", "license1": "1122753"},
    ]

    company = _company_from_contacts(contacts)

    assert company is not None
    assert company.name == "BuildRight Co"
