"""MCP server exposing ProjectIntel's compliance-aware crawler tools.

Every network tool here routes through ``PoliteHttpClient`` (robots.txt +
per-domain rate limiting + local HTTP cache), so any MCP client driving this
server inherits the same compliance guarantees the rest of the project enforces.
A single long-lived server process also means **one** global rate-limiter and
**one** robots cache across every call — stronger politeness than per-agent
in-process clients that each track their own timers.

The driver (a cloud routine, an agent SDK, etc.) supplies the
intelligence; this server supplies the *only* sanctioned way to touch the
network. Phase 1 covers source onboarding: discover -> dry-run -> propose.

Run it standalone with ``python -m bay_area_projectintel.mcp.server`` (stdio),
or register it in ``.mcp.json`` so an MCP client launches it automatically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from mcp.server.fastmcp import FastMCP

from bay_area_projectintel.compliance.politeness import PoliteHttpClient
from bay_area_projectintel.config import SourceConfig, load_config
from bay_area_projectintel.sources import build_source
from bay_area_projectintel.db import Database, stable_hash
from bay_area_projectintel.models import Company, Project, RawRecord
from bay_area_projectintel.sources.discover import (
    CityTarget,
    discover_cities,
    findings_to_dict,
)

mcp = FastMCP("projectintel")

# Singletons: one rate-limiter + one robots/HTTP cache for the whole process.
_config = load_config()
_settings = _config.settings
_http = PoliteHttpClient(
    _settings.cache_dir,
    _settings.user_agent,
    min_interval_seconds=_settings.politeness_min_interval,
)

# Vetted candidates land here, NOT in the live sources.yaml. A human promotes
# them after review — the safety valve for agent-discovered sources.
CANDIDATES_PATH = Path("config/sources.candidates.yaml")


def _unwrap_single_source(doc: Any) -> tuple[str, dict[str, Any]]:
    """Accept a source as ``{name: {...}}``, ``{sources: {name: {...}}}``, or a
    bare block carrying ``type`` + ``name``. Returns ``(name, config_dict)``."""
    if not isinstance(doc, dict):
        raise ValueError("config must be a YAML mapping")
    if "sources" in doc and isinstance(doc["sources"], dict):
        doc = doc["sources"]
    if "type" in doc and "name" in doc:
        return str(doc["name"]), dict(doc)
    if len(doc) == 1:
        name, inner = next(iter(doc.items()))
        if isinstance(inner, dict):
            return str(name), dict(inner)
    raise ValueError(
        "provide one source as {name: {type: ..., ...}} or a block with type + name"
    )


@mcp.tool()
def list_sources() -> list[dict[str, str]]:
    """List permit/RFP sources already configured in sources.yaml.

    Call this first when onboarding a city so you don't re-onboard one that is
    already covered. Reads the live config each call.
    """
    cfg = load_config()
    return [
        {
            "name": name,
            "type": source.type,
            "jurisdiction": source.jurisdiction,
            "county": source.county,
        }
        for name, source in cfg.sources.items()
    ]


@mcp.tool()
def discover_sources(
    city: str, county: str | None = None, max_candidates: int = 5
) -> list[dict[str, Any]]:
    """Probe the global Socrata catalog + ArcGIS Online for a city's permit datasets.

    Samples one row per candidate and buckets its columns into contractor-like vs
    owner/applicant-like. The ``verdict`` field is the gate that matters:

      * ``candidate``        — has a contractor-shaped field; meets the contact
                               hard-requirement; worth onboarding.
      * ``owner-only``       — the name column is the owner/applicant, not a
                               contractor; fails the contact gate. This city
                               likely needs a CPRA request or a portal path, not
                               a dataset.
      * ``no-contact-fields``— skip.

    robots.txt + rate limiting + cache are enforced. Use the returned ``api_url``,
    ``contractor_fields``, ``latest_date`` to draft a config block.
    """
    findings = discover_cities(
        [CityTarget(city, county)], _http, max_candidates=max_candidates
    )
    return findings_to_dict(findings)


@mcp.tool()
def http_get_json(
    url: str, params: dict[str, Any] | None = None, official_api: bool = False
) -> Any:
    """Polite GET returning parsed JSON. robots.txt + per-domain rate limit + cache enforced.

    Set ``official_api=True`` ONLY for genuine open-data APIs meant for
    programmatic access (Socrata / ArcGIS / CKAN / SAM.gov) — this skips the
    robots.txt check exactly as the rest of the pipeline does for official APIs.
    Leave it False for arbitrary or unknown pages.
    """
    return _http.get_json(url, params=params, check_robots=not official_api)


@mcp.tool()
def http_get_text(url: str, max_chars: int = 60000) -> str:
    """Polite GET returning raw text/HTML (robots + rate limit + cache enforced).

    Use for HTML pages that have no JSON API. Output is truncated to ``max_chars``
    to keep token cost bounded; the truncation is marked.
    """
    text = _http.get_text(url)
    if len(text) > max_chars:
        return f"{text[:max_chars]}\n...[truncated {len(text) - max_chars} chars]"
    return text


@mcp.tool()
def http_post_json(
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    official_api: bool = True,
) -> Any:
    """Polite POST returning parsed JSON, for EnerGov/Accela-style search endpoints.

    robots + rate limit + cache enforced; ``official_api`` (default True) skips
    the robots check for these known portal APIs.
    """
    return _http.post_json(
        url, payload, headers=headers, check_robots=not official_api
    )


@mcp.tool()
def dry_run_source(config_yaml: str, limit: int = 3) -> dict[str, Any]:
    """Validate a candidate source config by fetching a few records through the REAL adapter.

    ``config_yaml`` is a YAML block for ONE source — either the bare config (with
    ``type`` + ``name``) or a ``{name: {...}}`` mapping. Builds the actual source
    via ``build_source`` and fetches up to ``limit`` records through the
    compliance-aware client, then returns:

      * ``ok``           — did it validate and fetch?
      * ``payload_keys`` — union of column names actually seen in the records
                           (check your field_map references these),
      * ``records``      — the sample payloads (confirm a real contractor value,
                           not null / owner).

    Always run this before proposing. Never propose a block that didn't fetch
    cleanly.
    """
    try:
        doc = yaml.safe_load(config_yaml)
        name, raw = _unwrap_single_source(doc)
    except Exception as exc:  # noqa: BLE001 — report parse/shape errors to the agent
        return {"ok": False, "stage": "parse", "error": f"{type(exc).__name__}: {exc}"}

    try:
        cfg = SourceConfig.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "validate", "error": f"{type(exc).__name__}: {exc}"}

    try:
        source = build_source(name, cfg, _http, _settings)
        records: list[dict[str, Any]] = []
        keys: set[str] = set()
        for record in source.fetch(since=None, limit=limit):
            payload = dict(record.payload)
            keys.update(payload.keys())
            records.append(
                {"source_record_id": record.source_record_id, "payload": payload}
            )
            if len(records) >= limit:
                break
        return {
            "ok": True,
            "name": name,
            "fetched": len(records),
            "payload_keys": sorted(keys),
            "records": records,
        }
    except Exception as exc:  # noqa: BLE001 — adapter/network failures are data, not crashes
        return {"ok": False, "stage": "fetch", "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def propose_source_config(name: str, config_yaml: str, notes: str = "") -> dict[str, Any]:
    """Append a vetted candidate source to config/sources.candidates.yaml.

    NEVER writes the live config/sources.yaml — a human reviews and promotes the
    candidate. Run ``dry_run_source`` first; only propose configs that fetched
    cleanly. The block is stored under ``candidates:`` with ``pending_review:
    true`` and your ``notes``.
    """
    try:
        parsed = yaml.safe_load(config_yaml)
        if isinstance(parsed, dict) and "type" not in parsed and len(parsed) != 1:
            return {"ok": False, "error": "config_yaml must describe exactly one source block"}
        _name, block = _unwrap_single_source(
            parsed if isinstance(parsed, dict) and ("type" in parsed or len(parsed) == 1)
            else {name: parsed}
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    existing: dict[str, Any] = {}
    if CANDIDATES_PATH.exists():
        existing = yaml.safe_load(CANDIDATES_PATH.read_text(encoding="utf-8")) or {}
    bucket = existing.setdefault("candidates", {})

    block = dict(block)
    block["pending_review"] = True
    if notes:
        block["_notes"] = notes
    bucket[name] = block

    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_PATH.write_text(
        yaml.safe_dump(existing, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return {"ok": True, "written_to": str(CANDIDATES_PATH), "name": name}


@mcp.tool()
def render_page(url: str, max_chars: int = 50000) -> str:
    """Render a JS-heavy page with a real browser and return its visible text.

    For portals whose listing/results only appear after JavaScript runs. Uses
    Scrapling's DynamicFetcher (Playwright); robots.txt + rate limiting are still
    enforced and it never bypasses logins, paywalls, or CAPTCHAs. Requires the
    optional browser kernel (``pip install 'scrapling[fetchers]' && scrapling
    install``) — returns an install hint if it is missing.

    Prefer ``http_get_json`` / ``http_get_text`` first; reach for this only when
    the content genuinely needs JS to render.
    """
    try:
        from scrapling.fetchers import DynamicFetcher

        from bay_area_projectintel.enrichment.web import visible_text
    except Exception:  # noqa: BLE001 — missing optional kernel is a normal, reported state
        return "[browser kernel not installed — run: pip install 'scrapling[fetchers]' && scrapling install]"

    try:
        _http.ensure_allowed(url)  # robots.txt
    except PermissionError as exc:
        return f"[blocked by robots.txt: {exc}]"
    _http.throttle(url)  # per-domain rate limit

    try:
        page = DynamicFetcher.fetch(url, headless=True, network_idle=True)
    except Exception as exc:  # noqa: BLE001 — browser/runtime failures are data, not crashes
        return f"[render failed: {type(exc).__name__}: {exc}]"

    text = visible_text(page)
    if len(text) > max_chars:
        return f"{text[:max_chars]}\n...[truncated {len(text) - max_chars} chars]"
    return text


@mcp.tool()
def ingest_records(source_name: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist permit records you extracted from a no-API page into the pipeline.

    Use after reading a portal page (via ``http_get_text`` / ``render_page``) and
    extracting structured rows yourself — you are the normalizer here. Each record
    is a dict:

      * required: ``description`` (str)
      * optional: ``permit_number``, ``address``, ``project_date`` (ISO date),
        ``city``, ``county``, ``source_url``, ``company_name``, ``company_license``

    Records upsert idempotently on ``(source_name, permit_number | content-hash)``,
    so re-running a page is safe. Rows land as ``projects`` ready for the normal
    ``classify -> dedupe -> enrich -> export`` tail (run via the CLI, or the Phase 4
    orchestrator). Returns counts plus any skipped rows (e.g. missing description).
    """
    db = Database(_settings.db_path)
    db.migrate()
    written = 0
    skipped: list[dict[str, Any]] = []
    for index, rec in enumerate(records):
        description = str(rec.get("description") or "").strip()
        if not description:
            skipped.append({"index": index, "reason": "missing description"})
            continue
        payload = {key: value for key, value in rec.items() if value is not None}
        record_id = str(rec.get("permit_number") or stable_hash(payload))
        content_hash = stable_hash(payload)

        raw_id, _ = db.upsert_raw_record(
            RawRecord(
                source=source_name,
                source_record_id=record_id,
                payload=payload,
                content_hash=content_hash,
            )
        )
        company = (
            Company(name=str(rec["company_name"]), license_number=rec.get("company_license"))
            if rec.get("company_name")
            else None
        )
        db.upsert_project(
            Project(
                raw_record_id=raw_id,
                source=source_name,
                source_record_id=record_id,
                permit_number=rec.get("permit_number"),
                description=description,
                project_date=rec.get("project_date"),
                address=rec.get("address"),
                city=rec.get("city"),
                county=rec.get("county"),
                source_url=rec.get("source_url"),
                company=company,
                content_hash=content_hash,
            )
        )
        db.mark_raw_processed(raw_id)
        written += 1
    return {"ok": True, "source": source_name, "written": written, "skipped": skipped}


@mcp.tool()
def fetch_source(name: str, limit: int | None = None) -> dict[str, Any]:
    """Refresh ONE already-configured source (deterministic, 0 LLM cost): fetch + normalize.

    ``name`` must be a source in sources.yaml (see ``list_sources``). Fetch is
    incremental via the stored per-source watermark, so a plain refresh only pulls
    records newer than the last run. This is the green "fast path" — known sources
    cost nothing to refresh; reserve discovery/extraction for uncovered cities.

    Requires the full deps (``pip install -e '.[dev]'``); returns an install hint if
    they are missing.
    """
    try:
        from bay_area_projectintel import cli
    except Exception as exc:  # noqa: BLE001 — missing optional deps is a reported state
        return {"ok": False, "error": f"missing deps (pip install -e '.[dev]'): {type(exc).__name__}: {exc}"}

    cfg = load_config()
    if name not in cfg.sources:
        return {"ok": False, "error": f"unknown source '{name}'; configured: {list(cfg.sources)}"}
    try:
        cli.fetch(source=name, since=None, lookback_days=None, limit=limit)
    except Exception as exc:  # noqa: BLE001 — adapter/network failures are data
        return {"ok": False, "stage": "fetch", "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "source": name}


@mcp.tool()
def run_pipeline_tail(out_path: str = "leads.xlsx", enrich_browser: bool = False) -> dict[str, Any]:
    """Finish the pipeline on everything in SQLite: classify -> dedupe -> enrich -> export.

    Lets you complete an end-to-end run without shelling out, so a locked-down
    (no-Bash) unattended session can still produce the Excel. Deterministic rules +
    DeepSeek classification + public-data enrichment — **no LLM calls here**, so
    it does not add agent cost. Run after ``fetch_source`` / ``ingest_records``.

    ``enrich_browser`` opts into the Scrapling browser enricher (needs the kernel).
    Requires the full deps (``pip install -e '.[dev]'``); returns an install hint if
    they are missing.
    """
    try:
        from pathlib import Path as _Path

        from bay_area_projectintel import cli
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"missing deps (pip install -e '.[dev]'): {type(exc).__name__}: {exc}"}

    try:
        cli.classify(limit=None)
        cli.dedupe()
        cli.enrich(category=None, browser=enrich_browser, download_cslb=False, limit=None)
        cli.export(out=_Path(out_path), category=None)
    except Exception as exc:  # noqa: BLE001 — surface pipeline errors to the agent
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "exported_to": out_path}


@mcp.tool()
def list_pending_companies(
    limit: int = 50, category: str | None = None, shard: int = 0, shard_count: int = 1
) -> list[dict[str, Any]]:
    """List companies on leads that still lack BOTH email and phone — the enrichment targets.

    These are the rows the deterministic CSLB + conservative domain-guess pass
    couldn't reach. Each entry carries ``company_id`` (pass it to
    ``set_company_contact``), the name, any known website, license number, and a
    sample project. De-duplicated by company.

    For **parallel** enrichment, pass ``shard_count=K`` and ``shard=0..K-1`` so each
    worker gets a disjoint slice (partitioned by company_id) — K workers then never
    enrich the same company.
    """
    db = Database(_settings.db_path)
    db.migrate()
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    rows = db.get_projects_for_enrichment(
        category=category, limit=None if shard_count > 1 else limit
    )
    for row in rows:
        company_id = row["company_id"]
        if company_id is None or company_id in seen:
            continue
        if shard_count > 1 and company_id % shard_count != shard:
            continue
        seen.add(company_id)
        out.append(
            {
                "company_id": company_id,
                "company_name": row["company_name"],
                "license_number": row["license_number"],
                "website": row["website"],
                "city": row["city"],
                "sample_project": (row["description"] or "")[:80],
            }
        )
        if len(out) >= limit:
            break
    return out


@mcp.tool()
def find_company_website(company_name: str) -> dict[str, Any]:
    """Conservative deterministic guess of a company's website (domain variants + homepage verification).

    Reuses the project's existing discovery: builds candidate domains from the name
    and confirms the homepage text actually mentions the company. Returns
    ``{website|null}``. If it returns null, search the web for the company's official
    site, then confirm the URL with ``extract_contact_from_url`` (which checks the
    name matches) before trusting it. robots + rate limit enforced. Requires web deps.
    """
    try:
        from bay_area_projectintel.enrichment.web import PublicWebEnricher
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"web deps missing (pip install -e '.[dev]'): {exc}"}
    enricher = PublicWebEnricher(
        _http,
        _settings.web_max_discovery_candidates,
        _settings.web_max_contact_links,
        _settings.web_min_discovery_token_len,
    )
    return {"ok": True, "website": enricher.discover_website(company_name)}


@mcp.tool()
def extract_contact_from_url(url: str, company_name: str | None = None) -> dict[str, Any]:
    """Politely fetch a page and extract a public email/phone (mailto:/tel: links + text).

    If ``company_name`` is given, also reports whether the page's visible text matches
    that company (``matches_company``) — so you don't attach a different firm's contact.
    Only trust a contact when ``matches_company`` is true (or you've otherwise confirmed
    the site). robots.txt + rate limit + cache enforced. Requires web deps.
    """
    try:
        from bay_area_projectintel.enrichment.web import (
            extract_email,
            extract_phone,
            normalize_website,
            website_matches_company,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"web deps missing (pip install -e '.[dev]'): {exc}"}

    target = normalize_website(url) or url
    try:
        html = _http.get_text(target)
    except PermissionError as exc:
        return {"ok": False, "error": f"blocked by robots.txt: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    matched = website_matches_company(company_name, html, target) if company_name else None
    return {
        "ok": True,
        "url": target,
        "email": extract_email(html),
        "phone": extract_phone(html),
        "matches_company": matched,
    }


@mcp.tool()
def set_company_contact(
    company_id: int,
    email: str | None = None,
    phone: str | None = None,
    website: str | None = None,
) -> dict[str, Any]:
    """Write an enriched contact back to a company. Only fills empty fields (COALESCE).

    Call after confirming the contact belongs to this company (e.g.
    ``extract_contact_from_url`` returned ``matches_company: true``). Persisting a
    contact moves the lead out of "pending" on the next export.
    """
    db = Database(_settings.db_path)
    db.migrate()
    db.update_company_contact(company_id, email=email, phone=phone, website=website)
    return {
        "ok": True,
        "company_id": company_id,
        "set": {k: v for k, v in {"email": email, "phone": phone, "website": website}.items() if v},
    }


@mcp.tool()
def send_email_report(
    to: str | None = None,
    attach_path: str | None = None,
    subject: str | None = None,
    category: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Email the latest leads Excel + a summary via SMTP (Gmail by default).

    The body is the same data-update summary as ``notify``; the spreadsheet rides
    along as an attachment (defaults to the latest export). SMTP is not the web
    crawl path, so robots/rate-limit don't apply — but credentials and recipients
    must be configured: set PROJECTINTEL_SMTP_USER / PROJECTINTEL_SMTP_PASSWORD (a
    Gmail App Password), PROJECTINTEL_EMAIL_TO. Pass ``dry_run=True`` to write the
    composed ``.eml`` under ``data/`` instead of sending (no creds needed).
    """
    from dataclasses import replace

    from bay_area_projectintel.db import Database
    from bay_area_projectintel.notify import EmailChannel, build_summary, parse_recipients

    db = Database(_settings.db_path)
    db.migrate()

    attach = Path(attach_path) if attach_path else _settings.latest_excel_path
    note = build_summary(db.export_rows(category=category), latest_excel=None)
    if subject:
        note = replace(note, subject=subject)
    if attach and attach.exists():
        note = replace(note, attachments=(attach,))

    recipients = parse_recipients(to or _settings.email_to)
    if not recipients:
        return {"ok": False, "error": "no recipients (pass `to` or set PROJECTINTEL_EMAIL_TO)"}
    if not dry_run and (not _settings.smtp_user or not _settings.smtp_password):
        return {
            "ok": False,
            "error": "SMTP creds missing: set PROJECTINTEL_SMTP_USER + PROJECTINTEL_SMTP_PASSWORD",
        }

    channel = EmailChannel(
        host=_settings.smtp_host,
        port=_settings.smtp_port,
        user=_settings.smtp_user or "",
        password=_settings.smtp_password or "",
        sender=_settings.email_from or _settings.smtp_user or "",
        recipients=recipients,
        use_ssl=_settings.smtp_use_ssl,
        dry_run_dir=(_settings.db_path.parent if dry_run else None),
    )
    try:
        channel.send(note)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "recipients": list(recipients),
        "attached": [p.name for p in note.attachments],
        "dry_run": dry_run,
    }


def main() -> None:
    """Console entrypoint — runs the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
