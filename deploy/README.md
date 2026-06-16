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

---

## 最简部署（5 步，全在 Railway 网页里点）

部署只做一次，由你完成；之后操作者只用浏览器。所有路径已写死在镜像里，所以你**只需填 4 个值**。

1. **新建项目**：Railway → New Project → **Deploy from GitHub repo** → 选 `heyemlee/ai-crawler`。
2. **加存储**：进服务 → Variables 旁边的 **Volume** → New Volume → Mount Path 填 **`/data`**。
3. **填 4 个变量**（Variables 标签，Raw Editor 粘贴即可）：
   ```
   OPERATOR_PASSWORD=自己定一个访问密码
   PROJECTINTEL_SMTP_USER=ywu1286@gmail.com
   PROJECTINTEL_SMTP_PASSWORD=你的Gmail应用专用密码
   PROJECTINTEL_EMAIL_TO=默认收件人@example.com
   ```
4. **拿网址**：Settings → Networking → **Generate Domain**。
5. **发给操作者**：把「网址 + 访问密码」发过去。他们开浏览器、输密码、点「开始跑批」就行。

> Gmail 应用专用密码：https://myaccount.google.com/apppasswords （先开两步验证）。没有它邮件发不出去。
> 想让分类更准，可再加一个 `DEEPSEEK_API_KEY`（可选）。

下面是详细参考（含 CLI 部署、无人值守 cron、灌历史数据）。

---

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

**State paths — already baked into the image (no need to set these)**

The Dockerfile sets `PROJECTINTEL_DB_PATH`, `PROJECTINTEL_CACHE_DIR`,
`PROJECTINTEL_CSLB_MASTER_CSV`, `PROJECTINTEL_LATEST_EXCEL_PATH`,
`PROJECTINTEL_NOTIFY_LOG_PATH`, and `PROJECTINTEL_OUT` all under `/data`. Just mount
the volume at `/data` (step 1) and they persist — override only if you want a
different layout. `PORT` is set by Railway automatically; the app binds it.

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
