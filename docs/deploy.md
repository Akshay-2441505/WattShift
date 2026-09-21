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

1. **Neon:** create a project and copy the connection string (it starts with `postgresql://`). Create a second database (or
   branch) for tests only if you will run the backend tests against it; never point tests at the live one.
2. **Once, from your computer,** put that string in `~/.wattshift/.env` as `DATABASE_URL=...` and run, in `backend`:
   `python -m scripts.init_db` (creates the tables, the tariff, and today's prices), then
   `python -m scripts.onboard_site --company <name> --site <name> --gpus <n>`. It prints the site key **once**; put it in the
   agent's config file on the customer's machine.
3. **Render:** New > Blueprint > this repository. Fill in `DATABASE_URL`, `API_KEY` (make one: 32+ random characters) and
   `CORS_ORIGINS` (fill it after step 4, then redeploy). The free plan sleeps after about 15 minutes idle, and the
   in-process scheduler sleeps with it. Add a free pinger (cron-job.org or a GitHub Actions cron) that calls
   `https://<your-api>/health` every 10 minutes. The API also catches up missed work when it wakes.
4. **Vercel:** import the repository, root directory `frontend`, and set `VITE_API_URL` to the Render address (no trailing
   slash). The dashboard uses `#/` routes, so no rewrite rules are needed. Put the Vercel address into `CORS_ORIGINS` on Render.
5. **Check:** open the dashboard, submit a job (it asks for the API key), open **Live cluster** and pick your site.

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
