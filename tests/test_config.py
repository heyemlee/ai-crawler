from bay_area_projectintel.config import RuntimeSettings


def test_tunables_have_expected_defaults() -> None:
    s = RuntimeSettings()
    assert s.politeness_min_interval == 0.35
    assert s.dedupe_address_threshold == 92
    assert s.dedupe_title_threshold == 72
    assert s.web_max_discovery_candidates == 6
    assert s.browser_max_pages == 4


def test_env_overrides_tunables(monkeypatch) -> None:
    monkeypatch.setenv("PROJECTINTEL_DEDUPE_ADDRESS_THRESHOLD", "80")
    monkeypatch.setenv("PROJECTINTEL_POLITENESS_MIN_INTERVAL", "1.5")
    monkeypatch.setenv("PROJECTINTEL_BROWSER_MAX_PAGES", "2")

    s = RuntimeSettings()

    assert s.dedupe_address_threshold == 80
    assert s.politeness_min_interval == 1.5
    assert s.browser_max_pages == 2
