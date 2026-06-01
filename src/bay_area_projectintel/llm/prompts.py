from __future__ import annotations

from bay_area_projectintel.models import Category


def classification_prompt(description: str) -> str:
    categories = ", ".join(category.value for category in Category)
    return (
        "Classify this Bay Area construction/project lead into one category. "
        f"Allowed categories: {categories}. "
        "Return compact JSON with keys category, confidence, reason.\n\n"
        f"Description: {description}"
    )
