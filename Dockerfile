# Railway image for ProjectIntel.
#
# Default process = the operator web dashboard (projectintel-web): a browser UI to
# run the pipeline, download the spreadsheet, and configure the email recipient.
# The same image also ships the CLI, so a separate cron service can run
# deploy/railway-run.sh for an unattended weekly refresh. No Claude needed:
# classification falls back to DeepSeek (an API); everything else is plain Python.
FROM python:3.12-slim

# Unbuffered stdout so Railway logs stream live; no pip cache to keep the image lean.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the package with web extras. Copy only what the build needs first for caching.
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN pip install ".[web]"

# Deployment entrypoint for the optional cron service. Lives under deploy/ (tracked)
# because scripts/ is gitignored and would not be present in the repo Railway clones.
COPY deploy/railway-run.sh /app/deploy/railway-run.sh
RUN chmod +x /app/deploy/railway-run.sh

# All mutable state defaults to /data so deployment needs ZERO path config — just mount
# a Railway volume at /data and these persist. (Without a volume it still runs, just
# non-persistently: each run starts cold.) These ENV defaults apply only inside the
# image, so local development is unaffected. The operator then only sets a few secrets:
# OPERATOR_PASSWORD, PROJECTINTEL_SMTP_USER / PROJECTINTEL_SMTP_PASSWORD, PROJECTINTEL_EMAIL_TO.
ENV PROJECTINTEL_DB_PATH=/data/projectintel.sqlite3 \
    PROJECTINTEL_CACHE_DIR=/data/cache \
    PROJECTINTEL_CSLB_MASTER_CSV=/data/cslb/MasterLicenseData.csv \
    PROJECTINTEL_LATEST_EXCEL_PATH=/data/latest-leads.xlsx \
    PROJECTINTEL_NOTIFY_LOG_PATH=/data/notify.log \
    PROJECTINTEL_OUT=/data/leads.xlsx

# The web app binds $PORT (Railway sets it; defaults to 8000 locally).
CMD ["projectintel-web"]
