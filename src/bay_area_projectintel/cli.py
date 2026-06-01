from __future__ import annotations

import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from bay_area_projectintel.compliance.politeness import PoliteHttpClient
from bay_area_projectintel.config import load_config
from bay_area_projectintel.db import Database
from bay_area_projectintel.enrichment import EnrichmentPipeline
from bay_area_projectintel.enrichment.cslb import download_master_csv
from bay_area_projectintel.export.excel import export_excel
from bay_area_projectintel.llm.client import DeepSeekClassifier
from bay_area_projectintel.models import Category
from bay_area_projectintel.notify import FileChannel, StdoutChannel, build_summary, dispatch
from bay_area_projectintel.pipeline.classify import classify_with_rules
from bay_area_projectintel.pipeline.dedupe import dedupe_projects
from bay_area_projectintel.pipeline.normalize import normalize_raw_record
from bay_area_projectintel.report import build_report
from bay_area_projectintel.sources import build_source
from bay_area_projectintel.sources.cpra_import import (
    ImportStats,
    build_source_config,
    iter_csv_records,
    load_portals,
    portal_for,
    source_name_for,
)
from bay_area_projectintel.sources.discover import (
    CityTarget,
    discover_cities,
    findings_to_dict,
    findings_to_markdown,
)


app = typer.Typer(help="Bay Area ProjectIntel CLI")
console = Console()


def _runtime() -> tuple[Database, object]:
    config = load_config()
    db = Database(config.settings.db_path)
    db.migrate()
    return db, config


@app.command()
def fetch(
    source: str = typer.Option("datasf-building-permits", "--source", "-s"),
    since: str | None = typer.Option(None, "--since", help="ISO date/timestamp. Defaults to watermark or lookback window."),
    lookback_days: int | None = typer.Option(None, "--lookback-days"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    """Fetch public source records into SQLite and normalize changed records."""
    db, config = _runtime()
    if source not in config.sources:
        raise typer.BadParameter(f"Unknown source {source}. Available: {', '.join(config.sources)}")

    source_config = config.sources[source]
    client = PoliteHttpClient(
        config.settings.cache_dir,
        config.settings.user_agent,
        min_interval_seconds=config.settings.politeness_min_interval,
    )
    try:
        effective_since = since or db.get_watermark(source)
        if not effective_since:
            days = lookback_days or config.settings.default_lookback_days
            effective_since = (date.today() - timedelta(days=days)).isoformat()

        source_impl = build_source(source, source_config, client, config.settings)
        inserted = 0
        changed = 0
        for record in source_impl.fetch(since=effective_since, limit=limit):
            _, did_change = db.upsert_raw_record(record)
            inserted += 1
            changed += 1 if did_change else 0
        watermark = getattr(source_impl, "latest_watermark", None)
        if watermark:
            db.set_watermark(source, watermark)

        normalized = normalize(db)
        console.print(
            f"Fetched {inserted} records from {source} since {effective_since}; "
            f"{changed} new/changed, {normalized} normalized."
        )
        if getattr(source_impl, "rate_limited", False):
            console.print(
                "[yellow]Stopped early: SAM.gov rate limit (HTTP 429). Kept partial results; "
                "the free key has a request quota — wait a while, then rerun (the lookback "
                "window plus DB dedup will fill in the rest).[/yellow]"
            )
    finally:
        client.close()


@app.command("normalize")
def normalize_command() -> None:
    """Normalize changed raw records into project/company rows."""
    db, _ = _runtime()
    count = normalize(db)
    console.print(f"Normalized {count} raw records.")


def normalize(db: Database) -> int:
    config = load_config()
    count = 0
    for row in db.raw_records_needing_projects():
        source_name = row["source"]
        source_config = config.sources.get(source_name)
        if not source_config:
            continue
        project = normalize_raw_record(row, source_config)
        db.upsert_project(project)
        db.mark_raw_processed(int(row["id"]))
        count += 1
    return count


@app.command()
def classify(limit: int | None = typer.Option(None, "--limit")) -> None:
    """Classify unclassified projects with rules and optional DeepSeek fallback."""
    db, config = _runtime()
    normalize(db)
    llm = DeepSeekClassifier(config.settings)
    rows = db.get_unclassified_projects(limit=limit)
    counts: dict[str, int] = {}
    for row in rows:
        result = classify_with_rules(row["description"])
        if result.category == Category.OTHER and llm.enabled:
            try:
                result = llm.classify(row["description"]) or result
            except Exception as exc:
                console.print(f"[yellow]DeepSeek fallback failed for project {row['id']}: {exc}[/yellow]")
        db.set_project_classification(int(row["id"]), result.category, result.confidence)
        counts[result.category.value] = counts.get(result.category.value, 0) + 1

    table = Table(title="Classification")
    table.add_column("Category")
    table.add_column("Count", justify="right")
    for category, count in sorted(counts.items()):
        table.add_row(category, str(count))
    console.print(table)


@app.command()
def dedupe() -> None:
    """Mark cross-source duplicate projects (address + title fuzzy match)."""
    db, config = _runtime()
    stats = dedupe_projects(
        db,
        address_threshold=config.settings.dedupe_address_threshold,
        title_threshold=config.settings.dedupe_title_threshold,
    )
    console.print(
        f"Dedupe: scanned {stats['projects']} projects, "
        f"marked {stats['duplicates']} duplicates across {stats['groups']} groups."
    )


@app.command()
def enrich(
    category: str | None = typer.Option(None, "--category"),
    browser: bool = typer.Option(False, "--browser", help="Enable opt-in browser enrichment provider."),
    download_cslb: bool = typer.Option(False, "--download-cslb", help="Download/update CSLB License Master CSV before enrichment."),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    """Enrich company contact fields where possible."""
    db, config = _runtime()
    if category:
        Category(category)
    if download_cslb:
        console.print(f"Downloading CSLB License Master CSV to {config.settings.cslb_master_csv} ...")
        download_master_csv(config.settings.cslb_master_csv, config.settings.user_agent)

    client = PoliteHttpClient(
        config.settings.cache_dir,
        config.settings.user_agent,
        min_interval_seconds=config.settings.politeness_min_interval,
    )
    try:
        target_rows = db.get_projects_for_enrichment(category=category, limit=limit)
        target_licenses = [row["license_number"] for row in target_rows if row["license_number"]]
        pipeline = EnrichmentPipeline(
            db,
            client,
            config.settings.cslb_master_csv,
            enable_browser=browser,
            target_licenses=target_licenses,
            settings=config.settings,
        )
        stats = pipeline.run(category=category, limit=limit)
        console.print(f"Enrichment stats: {stats}")
    finally:
        client.close()


@app.command()
def export(
    out: Path = typer.Option(Path("leads.xlsx"), "--out", "-o"),
    category: str | None = typer.Option(None, "--category"),
) -> None:
    """Export category sheets and a Pending sheet to Excel."""
    db, config = _runtime()
    if category:
        Category(category)
    stats = export_excel(db, out, category=category)
    console.print(f"Exported {stats['exported']} leads to {out}; {stats['pending']} pending.")

    # Keep a stable pointer to the newest export so a scheduler / WeChat bridge can
    # always return "the latest Excel" without knowing the per-run filename.
    latest = config.settings.latest_excel_path
    if latest.resolve() != out.resolve():
        latest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out, latest)
        console.print(f"Updated latest pointer: {latest}")


@app.command()
def report(category: str | None = typer.Option(None, "--category")) -> None:
    """Print a lead summary: new today, contact coverage, high-value leads, and pending."""
    db, _ = _runtime()
    if category:
        Category(category)
    summary = build_report(db.export_rows(category=category))

    overview = Table(title="ProjectIntel Report")
    overview.add_column("Metric")
    overview.add_column("Value", justify="right")
    overview.add_row("Total leads", str(summary.total))
    overview.add_row("With contact", str(summary.with_contact))
    overview.add_row("Pending (no contact)", str(summary.pending))
    overview.add_row("Contact coverage", f"{summary.coverage:.1%}")
    overview.add_row("New today (🆕)", str(summary.new_today))
    overview.add_row("High-value leads", str(summary.high_value))
    overview.add_row("RFP leads (path A)", str(summary.rfp_leads))
    console.print(overview)

    by_cat = Table(title="By Category")
    by_cat.add_column("Category")
    by_cat.add_column("Total", justify="right")
    by_cat.add_column("With Contact", justify="right")
    by_cat.add_column("Coverage", justify="right")
    for stat in summary.by_category:
        by_cat.add_row(stat.category, str(stat.total), str(stat.with_contact), f"{stat.coverage:.1%}")
    console.print(by_cat)


@app.command()
def notify(
    to_file: bool = typer.Option(False, "--file", help="Also append the summary to the notify log."),
) -> None:
    """Emit a short data-update summary. Local channels only; OpenClaw adds WeChat later."""
    db, config = _runtime()
    note = build_summary(db.export_rows(), latest_excel=config.settings.latest_excel_path)
    channels = [StdoutChannel(printer=console.print)]
    if to_file:
        channels.append(FileChannel(config.settings.notify_log_path))
    failed = dispatch(channels, note)
    if failed:
        console.print(f"[yellow]Notification channels failed: {', '.join(failed)}[/yellow]")


@app.command(name="install-schedule")
def install_schedule(
    weekday: int = typer.Option(1, "--weekday", help="0=Sun..6=Sat (launchd Weekday). Default Monday."),
    hour: int = typer.Option(8, "--hour"),
    minute: int = typer.Option(0, "--minute"),
) -> None:
    """Generate a macOS launchd plist + wrapper for a weekly unattended run.

    Files are written into ./scripts; nothing is loaded automatically. Loading
    launchd jobs and waking the Mac modify your system, so the install commands
    are printed for you to run.
    """
    label = "com.projectintel.weekly"
    project_dir = Path.cwd()
    scripts_dir = project_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    wrapper = scripts_dir / "weekly-run.sh"
    plist = scripts_dir / f"{label}.plist"
    python_bin = Path(sys.executable)

    wrapper.write_text(
        "#!/bin/bash\n"
        "# Weekly ProjectIntel run. caffeinate keeps the Mac awake for the duration.\n"
        "set -euo pipefail\n"
        f'cd "{project_dir}"\n'
        f'if "{python_bin}" -m bay_area_projectintel.cli run; then\n'
        f'  "{python_bin}" -m bay_area_projectintel.cli notify --file\n'
        "else\n"
        f'  "{python_bin}" -m bay_area_projectintel.cli notify --file || true\n'
        "fi\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    log_dir = project_dir / "data"
    plist.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        f"  <key>Label</key><string>{label}</string>\n"
        "  <key>ProgramArguments</key>\n"
        f"  <array><string>/usr/bin/caffeinate</string><string>-i</string><string>{wrapper}</string></array>\n"
        "  <key>StartCalendarInterval</key>\n"
        f"  <dict><key>Weekday</key><integer>{weekday}</integer>"
        f"<key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>{minute}</integer></dict>\n"
        f"  <key>StandardOutPath</key><string>{log_dir / 'launchd.out.log'}</string>\n"
        f"  <key>StandardErrorPath</key><string>{log_dir / 'launchd.err.log'}</string>\n"
        "</dict>\n"
        "</plist>\n",
        encoding="utf-8",
    )

    console.print(f"Wrote {wrapper}\nWrote {plist}\n")
    console.print("To enable (runs on your machine — review first):")
    console.print(f"  cp {plist} ~/Library/LaunchAgents/")
    console.print(f"  launchctl load ~/Library/LaunchAgents/{label}.plist")
    console.print(
        f"To wake the Mac ~5 min early so the {hour:02d}:{minute:02d} job runs from sleep:"
    )
    console.print("  sudo pmset repeat wakeorpoweron MON 07:55:00")


# Default targets: South Bay cities we already know lack official permit APIs
# (already verified in Phase 1.5). The point of `discover` is to confirm rerunnably
# and surface any *new* dataset a city publishes later — pass --cities to override.
SOUTH_BAY_CITIES = [
    CityTarget("San Jose", "Santa Clara"),
    CityTarget("Sunnyvale", "Santa Clara"),
    CityTarget("Cupertino", "Santa Clara"),
    CityTarget("Santa Clara", "Santa Clara"),
    CityTarget("Mountain View", "Santa Clara"),
    CityTarget("Palo Alto", "Santa Clara"),
    CityTarget("Milpitas", "Santa Clara"),
    CityTarget("Los Gatos", "Santa Clara"),
    CityTarget("Saratoga", "Santa Clara"),
    CityTarget("Campbell", "Santa Clara"),
    CityTarget("Morgan Hill", "Santa Clara"),
    CityTarget("Gilroy", "Santa Clara"),
]


@app.command()
def discover(
    cities: str | None = typer.Option(
        None,
        "--cities",
        help='Comma-separated city names. Default = South Bay. Use "City:County,..." for county hints.',
    ),
    out: Path = typer.Option(Path("data/discover-report.md"), "--out", "-o"),
    json_out: Path | None = typer.Option(None, "--json", help="Also write findings as JSON."),
    max_candidates: int = typer.Option(5, "--max-candidates"),
) -> None:
    """Scan Socrata catalog + ArcGIS for city permit datasets.

    For each candidate dataset, samples 1 row, buckets fields into contractor-like
    vs owner/applicant-like, and reports newest record date. Only datasets with a
    contractor field meet the contact hard-requirement; the rest are flagged so a
    human can decide whether they are worth a CPRA request or browser path.
    """
    _, config = _runtime()
    targets: list[CityTarget] = SOUTH_BAY_CITIES
    if cities:
        targets = []
        for raw in cities.split(","):
            piece = raw.strip()
            if not piece:
                continue
            if ":" in piece:
                name, county = piece.split(":", 1)
                targets.append(CityTarget(name.strip(), county.strip()))
            else:
                targets.append(CityTarget(piece))

    client = PoliteHttpClient(
        config.settings.cache_dir,
        config.settings.user_agent,
        min_interval_seconds=config.settings.politeness_min_interval,
    )
    try:
        findings = discover_cities(targets, client, max_candidates=max_candidates)
    finally:
        client.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(findings_to_markdown(findings), encoding="utf-8")
    console.print(f"Wrote {len(findings)} findings to {out}")

    if json_out:
        import json

        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(findings_to_dict(findings), indent=2), encoding="utf-8")
        console.print(f"Wrote JSON findings to {json_out}")

    candidates = [f for f in findings if f.has_contractor]
    table = Table(title="Discover summary")
    table.add_column("City")
    table.add_column("Source")
    table.add_column("Verdict")
    table.add_column("Latest")
    for f in findings:
        table.add_row(f.city, f.source_kind, f.verdict(), f.latest_date or "-")
    console.print(table)
    if candidates:
        console.print(f"[green]{len(candidates)} contractor-shaped candidate(s) — review and add to sources.yaml.[/green]")
    else:
        console.print("[yellow]No contractor-shaped datasets — South Bay still needs CPRA / Accela path.[/yellow]")


@app.command(name="import-cpra")
def import_cpra(
    file: Path = typer.Option(..., "--file", "-f", help="CSV returned by the city's records office."),
    jurisdiction: str = typer.Option(..., "--jurisdiction", "-j", help="City name (must match a portals.yaml entry)."),
    portals_yaml: Path = typer.Option(Path("config/cpra/portals.yaml"), "--portals"),
) -> None:
    """Import a CPRA CSV response into raw_records + normalize.

    The CSV's columns map to the schema in docs/cpra-request-template.md. If a
    city uses different headings, add a per-city field_map override in portals.yaml.
    Re-running is safe — rows upsert on (source, permit_number).
    """
    if not file.exists():
        raise typer.BadParameter(f"CSV not found: {file}")
    if not portals_yaml.exists():
        raise typer.BadParameter(f"Portals yaml not found: {portals_yaml}")
    portals = load_portals(portals_yaml)
    portal = portal_for(portals, jurisdiction)
    if not portal:
        available = ", ".join(p.jurisdiction for p in portals)
        raise typer.BadParameter(
            f"No portal entry for '{jurisdiction}'. Available: {available}"
        )

    source_config = build_source_config(portal)
    source_name = source_name_for(portal.jurisdiction)
    db, _ = _runtime()

    stats = ImportStats(rows_read=0, raw_inserted=0, raw_changed=0, skipped=0)
    for record, skipped in iter_csv_records(file, source_name, source_config.field_map):
        stats.rows_read += 1
        if skipped:
            stats.skipped += 1
            continue
        _, did_change = db.upsert_raw_record(record)
        stats.raw_inserted += 1
        if did_change:
            stats.raw_changed += 1

    # Inline normalize so we don't have to mutate the global sources config.
    normalized = 0
    for row in db.raw_records_needing_projects():
        if row["source"] != source_name:
            continue
        project = normalize_raw_record(row, source_config)
        db.upsert_project(project)
        db.mark_raw_processed(int(row["id"]))
        normalized += 1

    console.print(
        f"CPRA import [{portal.jurisdiction}]: read {stats.rows_read}, "
        f"upserted {stats.raw_inserted} ({stats.raw_changed} changed), "
        f"skipped {stats.skipped} duplicate-rows, normalized {normalized}."
    )


@app.command()
def run(
    sources: list[str] = typer.Option(["datasf-building-permits"], "--source", "-s"),
    lookback_days: int = typer.Option(60, "--lookback-days"),
    out: Path = typer.Option(Path("leads.xlsx"), "--out", "-o"),
    limit: int | None = typer.Option(None, "--limit"),
    browser: bool = typer.Option(False, "--browser"),
) -> None:
    """Run fetch, classify, enrich, and export."""
    for source in sources:
        fetch(source=source, since=None, lookback_days=lookback_days, limit=limit)
    classify(limit=None)
    dedupe()
    enrich(category=None, browser=browser, download_cslb=False, limit=None)
    export(out=out, category=None)
    report(category=None)


if __name__ == "__main__":
    app()
