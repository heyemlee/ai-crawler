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

# All mutable state (SQLite DB, HTTP cache, CSLB CSV, Excel, operator config) must live
# on the Railway volume mounted at /data — point the PROJECTINTEL_* path env vars there.
# Without a volume the container filesystem is ephemeral and every run starts cold.
# The web app binds $PORT (Railway sets it; defaults to 8000 locally).
CMD ["projectintel-web"]
