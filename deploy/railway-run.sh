#!/usr/bin/env bash
# Deterministic weekly run on Railway: refresh configured sources → classify →
# dedupe → enrich → export → email. No Claude needed (DeepSeek does classification).
# State persists on the volume via the PROJECTINTEL_* path env vars (see deploy/README.md).
set -euo pipefail

# Space-separated `-s name` flags. Override with PROJECTINTEL_SOURCES to add/remove
# cities — e.g. append "-s samgov-construction" once SAM_API_KEY is set. Defaults to
# the four key-free permit sources.
SOURCES="${PROJECTINTEL_SOURCES:--s datasf-building-permits -s marin-building-permits -s sanjose-active-building-permits -s sunnyvale-energov-permits}"
LOOKBACK="${PROJECTINTEL_LOOKBACK_DAYS:-90}"
OUT="${PROJECTINTEL_OUT:-/data/leads.xlsx}"

echo "[railway-run] start: sources='${SOURCES}' lookback=${LOOKBACK} out=${OUT}"

# Word-splitting on $SOURCES is intentional (multiple -s flags).
# shellcheck disable=SC2086
projectintel run ${SOURCES} --lookback-days "${LOOKBACK}" --out "${OUT}"

# Best-effort email: a missing/wrong SMTP setting must never fail the data run, since
# the Excel is already exported and persisted on the volume.
if projectintel email --attach "${OUT}"; then
  echo "[railway-run] emailed ${OUT}"
else
  echo "[railway-run] email skipped/failed — check PROJECTINTEL_SMTP_* and PROJECTINTEL_EMAIL_TO" >&2
fi

echo "[railway-run] done"
