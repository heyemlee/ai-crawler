# Deploying on Railway — operator web dashboard

This deploys the **operator web dashboard** (`projectintel-web`): a browser UI so a
non-technical person can run the pipeline, download the leads spreadsheet, and set
who it gets emailed to — no terminal needed. The same image also contains the CLI,
so you can optionally add a second service for an unattended weekly cron.

No Claude is needed here (classification uses DeepSeek, an API). The **agentic**
half (auto-discovering *new* cities) runs separately as a local scheduled task; see
the main README.

Files: [`../Dockerfile`](../Dockerfile), [`../railway.toml`](../railway.toml),
[`railway-run.sh`](railway-run.sh) (cron entrypoint), [`../.dockerignore`](../.dockerignore).

## 1. The one thing you must not skip: a persistent volume

Railway's container filesystem is **ephemeral** — wiped on each deploy. The pipeline
is incremental: it relies on a SQLite DB (watermarks, dedupe state), an HTTP/CSLB
cache, the exported Excel, and the operator's saved settings all surviving between
runs. Create one volume mounted at **`/data`** and point the state env vars there.

> A Railway volume attaches to **one service only**. The web service owns `/data`.
> If you also want an unattended cron (step 5), it must be a **separate service with
> its own volume**, or just let the operator click "开始跑批".

## 2. Environment variables

**Access control**

| Var | Required | Notes |
|---|---|---|
| `OPERATOR_PASSWORD` | strongly recommended | If set, every page needs HTTP Basic auth (any username, this password). Without it the public URL is open to anyone. |

**Secrets**

| Var | Required | Notes |
|---|---|---|
| `DEEPSEEK_API_KEY` | optional | Classification fallback; without it, rules-only. |
| `PROJECTINTEL_SMTP_USER` | for email | Sending Gmail address. |
| `PROJECTINTEL_SMTP_PASSWORD` | for email | Gmail **App Password** (not the account password). |
| `PROJECTINTEL_EMAIL_TO` | optional | Seeds the default recipient; the operator can change it in the UI. |
| `SAM_API_KEY` | optional | Only if you add SAM.gov sources. |

**State paths — point everything at the `/data` volume**

```
PROJECTINTEL_DB_PATH=/data/projectintel.sqlite3
PROJECTINTEL_CACHE_DIR=/data/cache
PROJECTINTEL_CSLB_MASTER_CSV=/data/cslb/MasterLicenseData.csv
PROJECTINTEL_LATEST_EXCEL_PATH=/data/latest-leads.xlsx
PROJECTINTEL_NOTIFY_LOG_PATH=/data/notify.log
PROJECTINTEL_OUT=/data/leads.xlsx
```

`PORT` is set by Railway automatically — the app binds it. Don't hardcode it.

## 3. Deploy

```bash
npm i -g @railway/cli
railway login
railway init                 # or: railway link
railway volume add --mount-path /data
# set the variables from step 2 (dashboard, or `railway variables set KEY=VALUE`)
railway up                   # builds the Dockerfile and deploys
railway domain               # generate a public URL
```

`railway.toml` already sets `startCommand = projectintel-web`, a `/healthz` health
check, and restart-on-failure.

## 4. How the operator uses it

1. Open the Railway URL, enter the password (if you set `OPERATOR_PASSWORD`).
2. **邮件设置** → enter the recipient(s) and (optional) subject → 保存设置.
3. Click **▶ 开始跑批**. The page shows live status + a log tail; when it finishes
   the spreadsheet is emailed automatically.
4. **⬇ 下载最新表格** to download, or **✉ 立即重发最新表格** to re-send without re-running.

## 5. (Optional) a second service for an unattended weekly cron

Create another Railway service from the same repo, give it its own volume at `/data`
and the same env, and override its start command + add a cron schedule:

```
startCommand = "/app/deploy/railway-run.sh"
cronSchedule = "0 15 * * 1"   # Mon ~07:00 PT (cron is UTC)
restartPolicyType = "never"
```

## 6. (Optional) seed the volume with your existing data

A fresh volume starts empty, so the first run only has the last 90 days. To carry
over the leads you already built locally, copy your local `data/projectintel.sqlite3`
onto the volume once (`railway run` a shell and upload it). Subsequent runs stay
incremental.
