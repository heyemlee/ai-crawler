# Publishing the leads viewer (Cloudflare Pages / Vercel)

A free static page to browse the weekly-crawled leads — categorized, searchable,
with a 🆕 badge on newly crawled rows. The crawler runs wherever it already does;
it just exports a JSON the page reads.

Files: [`../site/index.html`](../site/index.html), [`../site/app.js`](../site/app.js),
generated data `../site/data/leads.json`, publish script [`publish-site.sh`](publish-site.sh).

## ⚠️ Privacy — read this first

The repo is **public**, and the data is ~10k business emails/phones. So:

- **The data file is NOT committed to git** (`.gitignore`'s `data/` rule covers
  `site/data/`). Only the viewer *code* is in the repo. Don't force-add the JSON.
- **Deploy the data directly** (wrangler upload below) — it never touches GitHub.
- **Gate the page** with Cloudflare Access (step 3) so only your team can open it.
  A bare `*.pages.dev` URL is reachable by anyone who has the link.

## 1. Generate the data

```bash
projectintel export-web --out site/data/leads.json   # also marks first_seen<7d as 🆕
```

## 2. Deploy to Cloudflare Pages (recommended — uploads the data, bypassing git)

```bash
npm i -g wrangler
wrangler login
wrangler pages deploy site --project-name leads-viewer
```

`wrangler pages deploy` uploads the actual `site/` directory (including the
gitignored `site/data/leads.json`), so the data reaches Cloudflare without ever
going through the public repo. You get a `https://leads-viewer.pages.dev` URL.

Or in one step each week: [`./publish-site.sh`](publish-site.sh) (export + deploy).

## 3. Protect it with Cloudflare Access (free, ~2 min)

Cloudflare dashboard → **Zero Trust → Access → Applications → Add application →
Self-hosted** → domain = your `leads-viewer.pages.dev` → add a policy: *Allow* →
*Emails* (list your team's emails) or *Emails ending in* `@ravv.com`. Now opening
the page requires an email one-time-code — randoms with the link are blocked.

## 4. Weekly update

Re-run step 1 + 2 (or `publish-site.sh`) after each crawl. The page redeploys with
fresh data; rows whose `first_seen` is within 7 days show the 🆕 badge and the
"只看新增" filter. Tune the window with `--new-window-days N`.

## Vercel alternative

`npm i -g vercel && vercel deploy site --prod` also works, but: (a) make sure the
data file is included (Vercel may skip gitignored files — add a `.vercelignore`
that doesn't list it, or upload via the dashboard), and (b) password-protecting a
Vercel site is a paid feature, whereas Cloudflare Access is free. For a private
data page, Cloudflare Pages + Access is the cleaner free path.
