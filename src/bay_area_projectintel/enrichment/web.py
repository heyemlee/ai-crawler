from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import httpx
from scrapling import Selector

from bay_area_projectintel.enrichment.cslb import clean_phone
from bay_area_projectintel.compliance.politeness import PoliteHttpClient
from bay_area_projectintel.enrichment.base import EnrichmentResult


CONTACT_PATHS = ("/contact", "/contact-us", "/about", "/about-us")
CONTACT_LINK_HINTS = ("contact", "about", "locations")
MAX_DISCOVERED_LINKS = 4
MAX_DISCOVERY_CANDIDATES = 6
MIN_DISCOVERY_TOKEN_LEN = 4
IGNORE_TAGS = ("script", "style", "noscript", "template")
COMPANY_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "llc",
    "lp",
    "ltd",
    "the",
}

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}")
PHONE_RE_FALLBACK = re.compile(r"\b1?\d{10}\b")
ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")


class PublicWebEnricher:
    provider = "public_web"

    def __init__(
        self,
        client: PoliteHttpClient,
        max_discovery_candidates: int = MAX_DISCOVERY_CANDIDATES,
        max_contact_links: int = MAX_DISCOVERED_LINKS,
        min_discovery_token_len: int = MIN_DISCOVERY_TOKEN_LEN,
    ):
        self.client = client
        self.max_discovery_candidates = max_discovery_candidates
        self.max_contact_links = max_contact_links
        self.min_discovery_token_len = min_discovery_token_len

    def enrich(self, company_name: str | None, website: str | None) -> EnrichmentResult:
        result = self.enrich_known_website(website)
        if result.status != "skipped":
            return result
        discovered = self.discover_website(company_name)
        if not discovered:
            return EnrichmentResult(self.provider, "not_found", detail="No reliable website candidate")
        return self.enrich_known_website(discovered)

    def enrich_known_website(self, website: str | None) -> EnrichmentResult:
        normalized = normalize_website(website)
        if not normalized:
            return EnrichmentResult(self.provider, "skipped", detail="No website")

        pages: list[tuple[str, Selector]] = []
        errors: list[str] = []
        for url in candidate_urls(normalized):
            try:
                page = _parse(self.client.get_text(url), url)
            except PermissionError as exc:
                return EnrichmentResult(self.provider, "blocked", website=normalized, detail=str(exc))
            except (httpx.HTTPError, OSError) as exc:
                errors.append(f"{url}: {exc.__class__.__name__}")
                continue
            pages.append((url, page))
            if url == normalized:
                for linked_url in _contact_links(page, normalized, self.max_contact_links):
                    try:
                        pages.append((linked_url, _parse(self.client.get_text(linked_url), linked_url)))
                    except PermissionError as exc:
                        return EnrichmentResult(self.provider, "blocked", website=normalized, detail=str(exc))
                    except (httpx.HTTPError, OSError) as exc:
                        errors.append(f"{linked_url}: {exc.__class__.__name__}")

        for url, page in pages:
            email = _email_from(page)
            phone = _phone_from(page)
            if email or phone:
                return EnrichmentResult(
                    self.provider,
                    "updated",
                    email=email,
                    phone=phone,
                    website=normalized,
                    detail=f"Found public contact on {url}",
                )

        detail = "No public email or phone found"
        if errors and not pages:
            detail = f"Unable to fetch website pages ({'; '.join(errors[:2])})"
        return EnrichmentResult(self.provider, "not_found", detail=detail)

    def discover_website(self, company_name: str | None) -> str | None:
        if not company_name:
            return None
        for website in website_candidates(
            company_name, self.max_discovery_candidates, self.min_discovery_token_len
        ):
            try:
                html = self.client.get_text(website)
            except (PermissionError, httpx.HTTPError, OSError):
                continue
            if website_matches_company(company_name, html, website):
                return website
        return None


def _parse(html: str | None, url: str = "") -> Selector:
    return Selector(content=html or "", url=url)


def normalize_website(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if not re.match(r"^https?://", cleaned, re.I):
        cleaned = f"https://{cleaned}"
    parsed = urlparse(cleaned)
    if not parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path or "", "", "", ""))


def candidate_urls(website: str) -> list[str]:
    parsed = urlparse(website)
    root = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    urls = [website]
    urls.extend(urljoin(root, path) for path in CONTACT_PATHS)
    return list(dict.fromkeys(urls))


def website_candidates(
    company_name: str,
    max_candidates: int = MAX_DISCOVERY_CANDIDATES,
    min_token_len: int = MIN_DISCOVERY_TOKEN_LEN,
) -> list[str]:
    tokens = company_tokens(company_name)
    if len(tokens) == 1 and len(tokens[0]) < min_token_len:
        # Short single-token acronyms (e.g. "GCI") are too ambiguous to guess a
        # domain for — they match unrelated companies. A wrong contact is worse
        # than none, so skip discovery rather than risk a false positive.
        return []
    variants = company_slug_variants(company_name)
    urls: list[str] = []
    for slug in variants:
        urls.append(f"https://{slug}.com")
        urls.append(f"https://www.{slug}.com")
    return urls[:max_candidates]


def company_slug_variants(company_name: str) -> list[str]:
    tokens = company_tokens(company_name)
    if not tokens:
        return []
    variants = ["".join(tokens)]
    if len(tokens) > 1:
        variants.append("-".join(tokens))
    if len(tokens) > 2:
        variants.append("".join(tokens[:2]))
    return list(dict.fromkeys(variants))


def company_tokens(company_name: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", company_name.lower().replace("&", " and "))
    return [word for word in words if word not in COMPANY_SUFFIXES and len(word) > 1]


def website_matches_company(company_name: str, html: str, website: str) -> bool:
    tokens = company_tokens(company_name)
    if not tokens:
        return False
    visible = visible_text(_parse(html)).lower()
    domain = urlparse(website).netloc.lower().removeprefix("www.")
    compact_domain = re.sub(r"[^a-z0-9]", "", domain.rsplit(".", 1)[0])
    compact_name = "".join(tokens)
    matched = sum(1 for token in tokens if len(token) > 2 and token in visible)
    required = 1 if len(tokens) == 1 else min(2, len(tokens))
    domain_matches = bool(compact_name and compact_name in compact_domain)
    if domain_matches:
        return matched >= 1
    return matched >= required


def contact_links(html: str, website: str) -> list[str]:
    return _contact_links(_parse(html, website), website)


def _contact_links(page: Selector, website: str, max_links: int = MAX_DISCOVERED_LINKS) -> list[str]:
    base = urlparse(website)
    urls: list[str] = []
    for href in _hrefs(page):
        href = href.strip()
        if not href or href.lower().startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(website, href)
        parsed = urlparse(url)
        if parsed.netloc != base.netloc:
            continue
        haystack = f"{parsed.path} {href}".lower()
        if any(hint in haystack for hint in CONTACT_LINK_HINTS):
            clean_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") or "/", "", "", ""))
            urls.append(clean_url)
        if len(dict.fromkeys(urls)) >= max_links:
            break
    return list(dict.fromkeys(urls))


def extract_email(html: str) -> str | None:
    return _email_from(_parse(html))


def extract_phone(html: str) -> str | None:
    return _phone_from(_parse(html))


def visible_text(page: Selector) -> str:
    return str(page.get_all_text(separator=" ", ignore_tags=IGNORE_TAGS))


def _hrefs(page: Selector) -> list[str]:
    return page.css("a::attr(href)").getall()


def _email_from(page: Selector) -> str | None:
    for href in _hrefs(page):
        if href.lower().startswith("mailto:"):
            candidate = _mailto_address(href)
            if candidate and not _looks_like_asset(candidate):
                return candidate
    for match in EMAIL_RE.finditer(visible_text(page)):
        email = match.group(0).strip(".,;:)")
        if not _looks_like_asset(email):
            return email
    return None


def _phone_from(page: Selector) -> str | None:
    for href in _hrefs(page):
        if href.lower().startswith("tel:"):
            phone = clean_phone(unquote(href.split(":", 1)[1]))
            if phone and phone.startswith("("):
                return phone
    text = visible_text(page)
    match = PHONE_RE.search(text) or PHONE_RE_FALLBACK.search(text)
    return clean_phone(match.group(0)) if match else None


def _mailto_address(href: str) -> str | None:
    value = unquote(href.split(":", 1)[1]).split("?", 1)[0].split(",", 1)[0].strip()
    return value.strip(".,;:)") or None


def _looks_like_asset(email: str) -> bool:
    return email.lower().endswith(ASSET_SUFFIXES)
