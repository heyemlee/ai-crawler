from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from rapidfuzz import fuzz

# Dedupe is intentionally conservative: losing a distinct lead by over-merging is
# worse than keeping a true duplicate. Two different tenant permits in one building
# share a street address and have similar TI-style titles, so we only merge when the
# full address (suite kept, not stripped) AND the title are both high-confidence.
ADDRESS_MATCH_THRESHOLD = 92
TITLE_MATCH_THRESHOLD = 72

_STREET_ABBR = {
    "street": "st",
    "avenue": "ave",
    "boulevard": "blvd",
    "drive": "dr",
    "road": "rd",
    "lane": "ln",
    "court": "ct",
    "place": "pl",
    "terrace": "ter",
    "highway": "hwy",
    "parkway": "pkwy",
    "square": "sq",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
}
# Canonicalize unit/suite markers to "unit <id>" (keeping the identifier) so that
# different suites in the same building stay distinct addresses instead of colliding.
_UNIT_RE = re.compile(
    r"(?:#|\b(?:suite|ste|unit|apt|apartment)\b)\.?\s*([\w-]+)",
    re.I,
)


@dataclass(frozen=True)
class DedupeRecord:
    id: int
    description: str
    address: str | None
    source: str
    has_company: bool
    first_seen: str


def normalize_address(value: str | None) -> str:
    if not value:
        return ""
    text = _UNIT_RE.sub(r" unit \1 ", value.lower())
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return " ".join(_STREET_ABBR.get(tok, tok) for tok in text.split())


def address_block_key(value: str | None) -> str:
    """A coarse key (street number + street-name prefix) used to avoid O(n^2) compares."""
    norm = normalize_address(value)
    if not norm:
        return ""
    number = next((tok for tok in norm.split() if tok.isdigit()), "")
    street = next((tok for tok in norm.split() if tok.isalpha()), "")
    if not number and not street:
        return ""
    return f"{number}:{street[:4]}"


def address_similarity(a: str | None, b: str | None) -> float:
    return fuzz.token_set_ratio(normalize_address(a), normalize_address(b))


def title_similarity(a: str | None, b: str | None) -> float:
    return fuzz.token_set_ratio(a or "", b or "")


def is_duplicate(
    a: DedupeRecord,
    b: DedupeRecord,
    address_threshold: int = ADDRESS_MATCH_THRESHOLD,
    title_threshold: int = TITLE_MATCH_THRESHOLD,
) -> bool:
    if not normalize_address(a.address) or not normalize_address(b.address):
        return False
    if address_similarity(a.address, b.address) < address_threshold:
        return False
    return title_similarity(a.description, b.description) >= title_threshold


def find_duplicate_groups(
    records: list[DedupeRecord],
    address_threshold: int = ADDRESS_MATCH_THRESHOLD,
    title_threshold: int = TITLE_MATCH_THRESHOLD,
) -> list[list[int]]:
    """Cluster records that refer to the same project. Returns groups of >= 2 ids."""
    parent = {r.id: r.id for r in records}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    blocks: dict[str, list[DedupeRecord]] = defaultdict(list)
    for record in records:
        key = address_block_key(record.address)
        if key:
            blocks[key].append(record)

    for group in blocks.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if is_duplicate(group[i], group[j], address_threshold, title_threshold):
                    union(group[i].id, group[j].id)

    clusters: dict[int, list[int]] = defaultdict(list)
    for record in records:
        clusters[find(record.id)].append(record.id)
    return [sorted(ids) for ids in clusters.values() if len(ids) > 1]


def choose_canonical(records: dict[int, DedupeRecord], group: list[int]) -> int:
    """Keep the most useful record: one with a company, then earliest seen, then lowest id."""

    def sort_key(rid: int) -> tuple[int, str, int]:
        record = records[rid]
        return (0 if record.has_company else 1, record.first_seen or "9999", record.id)

    return min(group, key=sort_key)


def plan_duplicates(
    records: list[DedupeRecord],
    address_threshold: int = ADDRESS_MATCH_THRESHOLD,
    title_threshold: int = TITLE_MATCH_THRESHOLD,
) -> list[tuple[int, int]]:
    """Return (duplicate_id, canonical_id) pairs to persist."""
    by_id = {r.id: r for r in records}
    pairs: list[tuple[int, int]] = []
    for group in find_duplicate_groups(records, address_threshold, title_threshold):
        canonical = choose_canonical(by_id, group)
        for rid in group:
            if rid != canonical:
                pairs.append((rid, canonical))
    return pairs


def dedupe_projects(
    db,
    address_threshold: int = ADDRESS_MATCH_THRESHOLD,
    title_threshold: int = TITLE_MATCH_THRESHOLD,
) -> dict[str, int]:
    rows = db.projects_for_dedupe()
    records = [
        DedupeRecord(
            id=int(row["id"]),
            description=row["description"] or "",
            address=row["address"],
            source=row["source"],
            has_company=row["company_id"] is not None,
            first_seen=str(row["first_seen"] or ""),
        )
        for row in rows
    ]
    pairs = plan_duplicates(records, address_threshold, title_threshold)
    db.reset_duplicate_flags()
    if pairs:
        db.mark_duplicates(pairs)
    groups = len({canonical for _, canonical in pairs})
    return {"projects": len(records), "duplicates": len(pairs), "groups": groups}
