from __future__ import annotations

import re

# The nine Bay Area counties' incorporated cities (plus a few common place names),
# normalized to lowercase with periods stripped. Primary signal for place matching.
BAY_AREA_CITIES: frozenset[str] = frozenset(
    _normalized
    for _name in (
        # San Francisco
        "San Francisco",
        # San Mateo
        "Atherton", "Belmont", "Brisbane", "Burlingame", "Colma", "Daly City",
        "East Palo Alto", "Foster City", "Half Moon Bay", "Hillsborough", "Menlo Park",
        "Millbrae", "Pacifica", "Portola Valley", "Redwood City", "San Bruno",
        "San Carlos", "San Mateo", "South San Francisco", "Woodside",
        # Santa Clara
        "Campbell", "Cupertino", "Gilroy", "Los Altos", "Los Altos Hills", "Los Gatos",
        "Milpitas", "Monte Sereno", "Morgan Hill", "Mountain View", "Moffett Field",
        "Palo Alto", "San Jose", "Santa Clara", "Saratoga", "Sunnyvale", "Stanford",
        # Alameda
        "Alameda", "Albany", "Berkeley", "Dublin", "Emeryville", "Fremont", "Hayward",
        "Livermore", "Newark", "Oakland", "Piedmont", "Pleasanton", "San Leandro",
        "Union City", "Castro Valley",
        # Contra Costa
        "Antioch", "Brentwood", "Clayton", "Concord", "Danville", "El Cerrito",
        "Hercules", "Lafayette", "Martinez", "Moraga", "Oakley", "Orinda", "Pinole",
        "Pittsburg", "Pleasant Hill", "Richmond", "San Pablo", "San Ramon",
        "Walnut Creek",
        # Marin
        "Belvedere", "Corte Madera", "Fairfax", "Larkspur", "Mill Valley", "Novato",
        "Ross", "San Anselmo", "San Rafael", "Sausalito", "Tiburon",
        # Napa
        "American Canyon", "Calistoga", "Napa", "St. Helena", "Yountville",
        # Solano
        "Benicia", "Dixon", "Fairfield", "Rio Vista", "Suisun City", "Vacaville",
        "Vallejo", "Travis AFB",
        # Sonoma
        "Cloverdale", "Cotati", "Healdsburg", "Petaluma", "Rohnert Park", "Santa Rosa",
        "Sebastopol", "Sonoma", "Windsor",
    )
    if (_normalized := re.sub(r"[^a-z0-9 ]", "", _name.lower()).strip())
)

# Inclusive 5-digit ZIP ranges for the cleanly-Bay-Area prefixes. The messy prefixes
# (95xxx Santa Clara vs Santa Cruz, 954xx Sonoma vs Lake/Mendocino) are intentionally
# left to city-name matching to avoid false positives.
BAY_AREA_ZIP_RANGES: tuple[tuple[int, int], ...] = (
    (94002, 94099),  # San Mateo / peninsula + Sunnyvale/Mountain View/Los Altos
    (94100, 94199),  # San Francisco
    (94301, 94309),  # Palo Alto
    (94401, 94499),  # San Mateo county
    (94500, 94599),  # East Bay / Solano / Contra Costa / Napa
    (94600, 94699),  # Oakland
    (94700, 94720),  # Berkeley
    (94800, 94899),  # Richmond / West Contra Costa
    (94900, 94999),  # Marin + Petaluma
    (95100, 95199),  # San Jose
    (95400, 95409),  # Santa Rosa core
)


def _normalize_city(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", value.lower()).strip()


def _zip_int(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\b(\d{5})\b", str(value))
    return int(match.group(1)) if match else None


def is_bay_area(city: str | None, zip_code: str | None, state: str | None = None) -> bool:
    """True if a place is in the nine Bay Area counties.

    Guarded by state == CA when known (so e.g. Richmond, VA does not match the
    Bay Area city of the same name). ZIP is the reliable signal; city name is the
    fallback for records whose ZIP prefix overlaps a non-Bay-Area county.
    """
    if state and state.upper() != "CA":
        return False
    zip_value = _zip_int(zip_code)
    if zip_value is not None and any(low <= zip_value <= high for low, high in BAY_AREA_ZIP_RANGES):
        return True
    if city and _normalize_city(city) in BAY_AREA_CITIES:
        return True
    return False


# Approximate 50-mile radius around San Jose for sources that only expose
# city / ZIP strings instead of coordinates. This intentionally stays
# conservative around ambiguous ZIP prefixes.
SAN_JOSE_50MI_CITIES: frozenset[str] = frozenset(
    _normalized
    for _name in (
        # Santa Clara / South Bay
        "San Jose", "Santa Clara", "Sunnyvale", "Cupertino", "Campbell",
        "Los Gatos", "Saratoga", "Milpitas", "Mountain View", "Palo Alto",
        "Los Altos", "Los Altos Hills", "Monte Sereno", "Morgan Hill",
        "Gilroy", "Moffett Field", "Stanford",
        # Peninsula / nearby San Mateo County
        "East Palo Alto", "Menlo Park", "Atherton", "Redwood City",
        "Portola Valley", "Woodside", "San Carlos", "Belmont",
        "Foster City", "San Mateo", "Hillsborough", "Burlingame",
        "Millbrae", "San Bruno", "South San Francisco", "Daly City",
        "Half Moon Bay", "Pacifica", "Brisbane", "Colma",
        # East Bay within the practical radius
        "Fremont", "Newark", "Union City", "Hayward", "Castro Valley",
        "Dublin", "Pleasanton", "Livermore", "San Leandro", "Alameda",
        "Oakland", "Berkeley", "Emeryville", "Albany",
        # Coastal / south of San Jose
        "Santa Cruz", "Scotts Valley", "Capitola", "Watsonville",
        "Aptos", "Soquel", "Hollister", "San Juan Bautista",
        # San Francisco is within roughly 50 straight-line miles of San Jose.
        "San Francisco",
    )
    if (_normalized := re.sub(r"[^a-z0-9 ]", "", _name.lower()).strip())
)

SAN_JOSE_50MI_ZIP_RANGES: tuple[tuple[int, int], ...] = (
    (94002, 94099),  # Peninsula + Mountain View / Sunnyvale / Los Altos
    (94100, 94199),  # San Francisco
    (94301, 94309),  # Palo Alto / Stanford
    (94401, 94499),  # San Mateo
    (94536, 94546),  # Fremont / Newark / Union City / Hayward
    (94550, 94552),  # Livermore / Castro Valley
    (94566, 94568),  # Pleasanton / Dublin
    (94600, 94720),  # Oakland / Berkeley
    (95000, 95099),  # Santa Clara County + Santa Cruz / Hollister area
    (95100, 95199),  # San Jose
)


def is_san_jose_50mi(city: str | None, zip_code: str | None, state: str | None = None) -> bool:
    """Approximate a San Jose-centered 50-mile filter from city/ZIP fields."""
    if state and state.upper() != "CA":
        return False
    zip_value = _zip_int(zip_code)
    if zip_value is not None and any(low <= zip_value <= high for low, high in SAN_JOSE_50MI_ZIP_RANGES):
        return True
    if city and _normalize_city(city) in SAN_JOSE_50MI_CITIES:
        return True
    return False
