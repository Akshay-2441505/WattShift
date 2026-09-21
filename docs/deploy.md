# Putting Wattshift online (Phase 8)

Three free services: **Neon** (the database), **Render** (the API), **Vercel** (the dashboard). The code is ready for it;
these steps need your accounts, so they are yours to do. Nothing here has been deployed.

## What the code already does for hosting

- `PRODUCTION=1` makes the API **refuse to start** without `API_KEY`. Every write endpoint (`POST /jobs`, `/backtest`,
  `/sites/{id}/mode`, `/sites/{id}/release-all`, `/demo/*`) then needs the `X-API-Key` header. The dashboard asks for the key
  when the server answers 401 and keeps it only for the browser tab.
- `CORS_ORIGINS` lists the dashboard's address; any other website's browser calls are refused.
- `GET /health` is the health check. `backend/requirements.txt` lists only what the API needs. `render.yaml` describes the
  Render service (secrets are typed into Render, not stored in the repo).
- The agent talks to `POST /agent/v1/sync` with its own per-site key (`X-Site-Key`), separate from `API_KEY`.

## Steps

1. **Neon (a NEW project, not the one you develop in):** create a project named `wattshift-hosted` in the Singapore region and copy
   the **direct** connection string (the host has no `-pooler` in it; it ends `?sslmode=require`). Compute hours are counted per
   project, so keeping the hosted database apart from your development and test databases stops local work from using up the
   hosted site's free hours. Never point the tests at the hosted database.
2. **Once, from your computer,** save that string in `~/.wattshift/.env` as `HOSTED_DATABASE_URL=...`, then create the tables, the
   tariff and today's prices in it (the API key line is added the same way, see step 3):
   `DATABASE_URL=<that string> python -m scripts.init_db` from `backend`. (The tables, the tariff catalogue and the audit-log guard are
   all created by that command.)
3. **Render:** New > Blueprint > this repository, region Singapore. Fill in `DATABASE_URL` (the hosted string), `API_KEY` (32+ random
   characters; keep a copy in `~/.wattshift/.env` as `HOSTED_API_KEY`) and `CORS_ORIGINS` (the Vercel address, filled in after step 4, then
   redeploy). `PRODUCTION=1` and `REPLAN_SECONDS=1800` are already in `render.yaml`.
4. **Vercel:** import the repository, root directory `frontend`, and set `VITE_API_URL` to the Render address (no trailing
   slash). The dashboard uses `#/` routes, so no rewrite rules are needed. Put the Vercel address into `CORS_ORIGINS` on Render.
5. **Check:** open the dashboard, wait for the API to wake (about a minute the first time), open **Prices today**, submit a job
   from **Try it** (it asks for the API key).

## Staying inside the free plans

- **Render** sleeps the API after 15 minutes without visitors and wakes it in about a minute. That is fine for a showcase, and the
  dashboard says "waiting for the API to come up" while it wakes. **Do not add a keep-alive pinger.** It would use nearly all of the
  750 monthly hours and keep the database awake.
- **Neon's** free compute hours (100 a month per project) are only used while something is asking the database questions. On a hosted
  API with no Kaggle provider the job loop runs every 30 minutes (`IDLE_DISPATCH_SECONDS`), re-planning every 30 minutes
  (`REPLAN_SECONDS`), prices are fetched every 3 hours, and a dashboard tab that is not on screen stops polling. If the hours run out,
  the database pauses until next month; nothing is deleted.
- Do not run your own tests, the demo or a local scheduler against the hosted database.

## Security settings (see `docs/security/2026-09-21-audit.md`)

- `PRODUCTION=1` also refuses to start with `DEMO_MODE=1`, and switches the interactive API docs off. Never set `DEMO_MODE` on a hosted API.
- `MAX_ACTIVE_JOBS` (default 50) caps how many jobs can wait or run at once; more get a 429. Every Kaggle job costs two GPU runs.
- `frontend/vercel.json` sets the dashboard's Content-Security-Policy and other browser protections. Its `connect-src` allows
  `https://*.onrender.com`; if your API lives elsewhere, put its address there.
- Site agents must use an `https://` cloud address (plain `http://` only for localhost), and on Linux the key file must be `chmod 600`.
- Put a rate limiter in front of the API (for example Cloudflare's free plan) before sharing the address widely.

## Known limits (be honest about these before showing customers)

- The read endpoints (`GET /sites`, `/sites/{id}/view`, `/jobs`, `/forecast`, ...) are open, like the rest of the dashboard.
  That is fine for demo data. Before a real customer's job names and sizes are on it, the read endpoints need a login.
- The Kaggle runner needs a Kaggle token on the host, which Render does not have; hosted use is for the Slurm agent path and
  the dashboard. The Kaggle demo runs from your own computer.
- The backtest keeps its results in memory, so a restart (or a free-plan sleep) forgets them.
