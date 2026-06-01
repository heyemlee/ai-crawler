from bay_area_projectintel.geo import is_bay_area


def test_bay_area_city_name_match() -> None:
    assert is_bay_area("Palo Alto", None, "CA")
    assert is_bay_area("Dublin", None, "CA")
    assert is_bay_area("St. Helena", None, "CA")  # period-insensitive


def test_bay_area_zip_catches_garbled_city() -> None:
    # Source city is wrong but the ZIP is Bay Area (Moffett Field / Travis AFB).
    assert is_bay_area("LINDA", "94035", "CA")
    assert is_bay_area("LINDA", "94535", "CA")


def test_rejects_california_but_not_bay_area() -> None:
    assert not is_bay_area("Fresno", "93701", "CA")
    assert not is_bay_area("Salinas", "93901", "CA")
    assert not is_bay_area(None, "92243", "CA")  # El Centro


def test_state_guard_rejects_same_name_out_of_state() -> None:
    # Richmond, VA must not match the Bay Area city of the same name.
    assert not is_bay_area("Richmond", None, "VA")
    assert is_bay_area("Richmond", None, "CA")


def test_unknown_place_is_not_bay_area() -> None:
    assert not is_bay_area(None, None, None)
