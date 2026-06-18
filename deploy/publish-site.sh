#!/usr/bin/env bash
# Regenerate the viewer data and publish it to Cloudflare Pages.
# The data is uploaded directly (never committed), keeping the 10k contacts off the
# public GitHub repo. Set CF_PAGES_PROJECT to override the Pages project name.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="${CF_PAGES_PROJECT:-leads-viewer}"
PY="${PYTHON:-.venv/bin/projectintel}"

echo "[publish-site] exporting data…"
"$PY" export-web --out site/data/leads.json

echo "[publish-site] deploying to Cloudflare Pages project '$PROJECT'…"
wrangler pages deploy site --project-name "$PROJECT"

echo "[publish-site] done"
