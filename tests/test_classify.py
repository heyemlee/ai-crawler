from bay_area_projectintel.models import Category
from bay_area_projectintel.pipeline.classify import classify_with_rules


def test_restaurant_retail_rule() -> None:
    result = classify_with_rules("Tenant improvement for a new restaurant kitchen")

    assert result.category == Category.RESTAURANT_RETAIL
    assert result.confidence >= 0.8


def test_other_fallback() -> None:
    result = classify_with_rules("Replace existing window in kind")

    assert result.category == Category.OTHER
