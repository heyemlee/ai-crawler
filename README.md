# Bay Area ProjectIntel

Local CLI for discovering Bay Area B2B project leads from public sources.

Pipeline: `fetch → classify → dedupe → enrich → export` (each step is idempotent,
state lives in SQLite, and runs are incremental).

```bash
projectintel fetch --source datasf-building-permits --since 2026-03-28
projectintel classify
projectintel dedupe
projectintel enrich
projectintel export --out leads.xlsx
```

Or run the full pipeline across multiple sources in one command:

```bash
projectintel run \
  -s datasf-building-permits \
  -s marin-building-permits \
  -s samgov-construction \
  --lookback-days 90 --out leads.xlsx
```

`run` defaults to `datasf-building-permits` only — pass `-s` for each source you
want. It chains fetch → classify → dedupe → enrich → export → report.

## Data sources

Configured in `config/sources.yaml`. Adding a Socrata permit city is config-only —
no code — via a `field_map` that maps the dataset's columns to project fields:

- `datasf-building-permits` — San Francisco; contractor comes from a joined
  contacts dataset (`contacts_dataset_id`).
- `marin-building-permits` — Marin County; a county-level dataset where the
  contractor and license live on the permit record itself (`company_name` /
  `company_license` in the `field_map`, plus a per-record `city` column).
- `samgov-construction` — federal RFPs (path A), opt-in, see below.

To add another Socrata city, find its domain + dataset id (e.g. via the Socrata
catalog API), confirm the column names, and add an entry with a `field_map`.
Defaults follow the DataSF schema, so only differing columns need overriding.

To enrich contractor phone numbers from CSLB's public License Master CSV:

```bash
projectintel enrich --download-cslb
projectintel export --out leads-cslb.xlsx
```

`--download-cslb` downloads the current statewide CSLB License Master CSV to
`.cache/projectintel/cslb/MasterLicenseData.csv`. Later runs can omit the flag
and reuse the cached file:

```bash
projectintel enrich
```

`projectintel enrich` also checks public company websites for an email address
or phone number. If SQLite already has a website, it uses that. Otherwise it
generates a small set of conservative candidate domains from the company name,
verifies that the homepage text matches the company, then fetches the homepage
plus common contact/about pages with robots.txt checks, per-domain rate
limiting, and the local HTTP cache. HTML is parsed with Scrapling's `Selector`
(CSS) — `mailto:`/`tel:` links and contact pages are extracted structurally
rather than by regex. Single short acronym names (e.g. "GCI") are skipped to
avoid matching an unrelated company.

### Browser enrichment (opt-in)

Some contact pages are rendered by JavaScript. `--browser` renders them with a
real browser (Scrapling's `DynamicFetcher` / Playwright). It still enforces
robots.txt and rate limiting, and never bypasses logins, paywalls, or CAPTCHAs.
The browser kernel is an optional dependency:

```bash
pip install -e ".[browser]"   # installs scrapling[fetchers]
scrapling install              # downloads the browser binaries
projectintel enrich --browser
```

Without the kernel installed, `--browser` degrades gracefully (the provider
reports "skipped" with an install hint) and the rest of enrichment still runs.

## Deduplication

```bash
projectintel dedupe
```

Marks cross-source duplicate projects (same project appearing in more than one
dataset). Matching is deliberately conservative — losing a distinct lead by
over-merging is worse than keeping a true duplicate — so it only merges when both
the normalized address (suite/unit kept, so different suites in one building stay
distinct) and the title clear high `rapidfuzz` thresholds. Duplicates are not
deleted: the canonical row (preferring one with a contact) is kept and the others
get a `duplicate_of` marker, so the decision is auditable and re-runnable.
Enrich, export, and report all hide rows flagged as duplicates.

## SAM.gov RFPs (path A)

Federal RFPs on SAM.gov carry an issuing-agency point of contact, so they
satisfy the contact requirement without enrichment. Register for a free key at
[sam.gov](https://sam.gov), set `SAM_API_KEY` in `.env`, then opt in:

```bash
projectintel fetch --source samgov-construction --lookback-days 30
projectintel classify
projectintel export --out leads.xlsx
```

Results are filtered to the nine Bay Area counties client-side (`region: bay_area`
in `config/sources.yaml`) using a city list plus clean ZIP ranges, guarded by
state == CA. Adjust `naics_codes` or set `region: CA` for state-wide. SAM.gov is
an official API, so robots.txt is not consulted (per-domain rate limiting and the
local cache still apply).

The free SAM key has a request quota. Because Bay Area filtering discards most
national RFPs, fetching pages deep can trip a rate limit (HTTP 429); the fetch
caps pages per NAICS code (`max_pages`) and stops gracefully on 429, keeping
partial results. Each run re-pulls the lookback window and dedups in SQLite (SAM
has no ascending sort, so no watermark is stored), so rerunning later fills in
the rest.

## Report

```bash
projectintel report
```

Prints a summary: total leads, contact coverage overall and per category, new
leads found today, high-value leads (a reachable contact plus a CSLB license or
an RFP point of contact), and pending count. `run` prints this automatically at
the end.

## Notifications & scheduling

`export` and `run` also copy the workbook to a stable path
(`data/latest-leads.xlsx` by default) so an automation can always fetch "the
latest Excel" without knowing the per-run filename.

```bash
projectintel notify          # print a short data-update summary
projectintel notify --file   # also append it to data/notify.log
```

`notify` uses pluggable channels (stdout and file today). To run weekly and
unattended on macOS, generate a launchd job:

```bash
projectintel install-schedule            # default: Monday 08:00
```

This writes `scripts/weekly-run.sh` (wraps `run` then `notify` under
`caffeinate` so the Mac stays awake mid-run) and a launchd plist into `scripts/`.
Nothing is loaded automatically — loading a launchd job and waking the Mac modify
your system, so the commands are printed for you to review and run:

```bash
cp scripts/com.projectintel.weekly.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.projectintel.weekly.plist
sudo pmset repeat wakeorpoweron MON 07:55:00   # wake ~5 min before the job
```

If the machine is off the job will not run; if asleep, the `pmset` wake above
lets the 08:00 job start. A WeChat bridge (OpenClaw) can later trigger these same
fixed commands and add a WeChat notification channel — no new pipeline code
needed.

## Compliance

- **API-first.** Official APIs / open data are used wherever they exist; pages are
  only scraped when there is no API.
- **robots.txt is always checked** for scraped pages (official APIs excepted),
  with conservative per-domain rate limiting, a contactable User-Agent, and a
  local HTTP cache to avoid repeat requests.
- **No bypassing.** Browser automation is opt-in and only for public, no-login
  pages; it never bypasses logins, paywalls, or CAPTCHAs.
- **Public contacts only.** Only publicly published contact details are collected.
  DeepSeek receives only project description text, for classification.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Configuration

All runtime knobs live in `RuntimeSettings` (`config.py`) and are overridable via
`PROJECTINTEL_*` environment variables or `.env` — no code edits needed. Data
sources stay in `config/sources.yaml`. Tunables (see `.env.example` for the full
list with defaults):

| Env var | Default | What it controls |
|---|---|---|
| `PROJECTINTEL_DEFAULT_LOOKBACK_DAYS` | `90` | Fetch window when there is no watermark/`--since` |
| `PROJECTINTEL_POLITENESS_MIN_INTERVAL` | `0.35` | Min seconds between requests to one domain |
| `PROJECTINTEL_DEDUPE_ADDRESS_THRESHOLD` | `92` | Dedupe address match cutoff (0-100) |
| `PROJECTINTEL_DEDUPE_TITLE_THRESHOLD` | `72` | Dedupe title match cutoff (0-100) |
| `PROJECTINTEL_WEB_MAX_DISCOVERY_CANDIDATES` | `6` | Candidate domains tried per company |
| `PROJECTINTEL_WEB_MAX_CONTACT_LINKS` | `4` | Contact/about links crawled per site |
| `PROJECTINTEL_WEB_MIN_DISCOVERY_TOKEN_LEN` | `4` | Min single-token length before guessing a domain |
| `PROJECTINTEL_BROWSER_MAX_PAGES` | `4` | Pages rendered per company in browser enrichment |
| `PROJECTINTEL_LATEST_EXCEL_PATH` | `data/latest-leads.xlsx` | Stable pointer to the newest export |

## Notes

- DataSF / Marin permits are fetched through Socrata APIs; fetch is incremental
  (a per-source watermark), so a plain re-run only pulls records newer than the
  last run. To backfill history, pass an explicit `--since` (it overrides the
  watermark).
- CSLB License Master CSV provides public contractor phone numbers, but not email addresses.
- Public website discovery is conservative and may leave many companies pending rather than risk a bad match.
- Browser enrichment is opt-in (`--browser`) and requires the optional browser kernel.
- Cross-source duplicates are marked (not deleted) and hidden from export/report.
- Category sheets in Excel include only rows with email or phone.
- Rows without contact details are preserved in `待补全 (Pending)`.
