from __future__ import annotations

import re

from bay_area_projectintel.models import Category, ClassificationResult


RULES: list[tuple[Category, float, list[str]]] = [
    (Category.PUBLIC_WORKS, 0.9, [r"\bstreet\b", r"\bsidewalk\b", r"\bsewer\b", r"\butility\b", r"\bpublic works\b"]),
    (Category.HOSPITALITY_REMODEL, 0.88, [r"\bhotel\b", r"\bmotel\b", r"\bhospitality\b"]),
    (Category.RESTAURANT_RETAIL, 0.86, [r"\brestaurant\b", r"\bretail\b", r"\bcafe\b", r"\bbar\b", r"\bkitchen\b"]),
    (Category.OFFICE_LAB, 0.84, [r"\boffice\b", r"\blab\b", r"\blaboratory\b", r"\br&d\b"]),
    (Category.COMMERCIAL_TI, 0.82, [r"\btenant improvement\b", r"\bti\b", r"\bcommercial\b", r"\bstorefront\b"]),
    (Category.RESIDENTIAL_REMODEL, 0.8, [r"\bremodel\b", r"\brenovation\b", r"\baddition\b", r"\badu\b", r"\bresidential\b", r"\bapartment\b"]),
    (Category.GC_SUBCONTRACT, 0.78, [r"\bsubcontract\b", r"\bstructural\b", r"\bmechanical\b", r"\belectrical\b", r"\bplumbing\b"]),
]


def classify_with_rules(description: str, existing_use: str | None = None, proposed_use: str | None = None) -> ClassificationResult:
    text = " ".join(part for part in (description, existing_use or "", proposed_use or "") if part).lower()
    for category, confidence, patterns in RULES:
        for pattern in patterns:
            if re.search(pattern, text):
                return ClassificationResult(category=category, confidence=confidence, reason=f"Matched {pattern}")
    return ClassificationResult(category=Category.OTHER, confidence=0.55, reason="No high-confidence rule matched")
