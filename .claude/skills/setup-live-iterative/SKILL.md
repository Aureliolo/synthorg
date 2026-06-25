---
description: "Stand up local live-reload frontend + backend dev servers against the running Docker DB/NATS, stop the Docker frontend/backend, and capture full logs for live iterative development"
argument-hint: "[--restart-backend] [--stop] [--status]"
allowed-tools:
  - Bash
  - PowerShell
  - Read
  - Write
  - BashOutput
---

# setup-live-iterative

Stand up **local** frontend + backend dev servers in the current worktree so
code changes apply live (Vite HMR + uvicorn restart) and every error is
visible in one log. The Docker **frontend + backend** containers are stopped;
**Postgres + NATS stay running** so the local backend reuses the same data,
secrets, and admin session.

Topology after setup:

```
browser :3000  ->  Vite dev server (web/, HMR)  --/api proxy-->  :3001
                                                                  |
                            local uvicorn (scripts/dev/run_api.py, SelectorEventLoop)
                                                                  |
                        container Postgres (:3002)  +  NATS (:3003)   <- left running
```

## Helper scripts (committed)

- `scripts/dev/backend_dev.mjs` - launches uvicorn on `127.0.0.1:3001`, reads
  the stopped backend container's env via `docker inspect` (secrets never hit
  the transcript), repoints `@postgres:5432` -> `:3002` and `nats:4222` ->
  `:3003`, sets `SYNTHORG_DEV_AUTH_BYPASS=true` (enables the password-free
  `/auth/dev-login` so the Vite frontend auto-logs-in as the existing admin),
  mirrors all output to a log file.
- `scripts/dev/run_api.py` - the uvicorn entrypoint pinned to a Windows
  `SelectorEventLoop` (psycopg's async pool cannot drive the default Proactor
  loop).

## Procedure

Default invocation (no args) does the full bring-up:

1. **Preconditions.** Confirm a git repo (`git rev-parse --is-inside-work-tree`).
   Confirm Docker is up and Postgres + NATS are running:
   `docker ps --format '{{.Names}}'` should list `data-postgres-1` and
   `data-nats-1`. If not, tell the user to `docker compose up -d postgres nats`
   (do not start them silently).

2. **Stop the Docker frontend + backend only** (leave DB/NATS):
   `docker stop data-web-1 data-backend-1` (ignore "no such container" -- on a
   fresh worktree they may already be down). Never stop `data-postgres-1` /
   `data-nats-1`.

3. **Free the ports.** Kill any stale local dev processes on 3000/3001:
   stop prior `backend_dev.mjs` / `run_api.py` / vite processes (PowerShell:
   match `backend_dev\.mjs|run_api\.py` and the `web` vite dev server).

4. **Launch the backend** (background), logging to a stable file:
   `node scripts/dev/backend_dev.mjs C:/tmp/synthorg-backend.log`
   Wait for `GET http://127.0.0.1:3001/api/v1/healthz` to return 200.

5. **Launch the frontend** (background) on port 3000 so existing bookmarks /
   the backend's expected origin match, with full logs:
   `bash -c "cd web && npm run dev -- --port 3000 --strictPort"` redirected to
   `C:/tmp/synthorg-dev-server.log` (use the Bash tool's background mode; do
   NOT use shell redirects to create the file -- pass the log path to the
   process or tee within the launched command's own shell). Wait for Vite
   "ready".

6. **Report.** Print the URLs (`http://localhost:3000`), both log paths, and a
   one-line "edit -> save -> browser updates" reminder. Backend Python changes
   need a backend restart (`--restart-backend`); web changes hot-reload.

## Sub-commands

- `--restart-backend`: stop the local uvicorn and relaunch `backend_dev.mjs`
  (used after editing `src/synthorg/**`). The frontend keeps running.
- `--status`: show whether local :3000 / :3001 respond and tail both logs.
- `--stop`: kill the local FE+BE dev processes. Optionally restart the Docker
  stack (`docker start data-backend-1 data-web-1`) if the user wants Docker
  back.

## Notes

- **Logs are the contract.** Always surface BOTH `C:/tmp/synthorg-backend.log`
  and `C:/tmp/synthorg-dev-server.log` so the user sees every error.
- **Auth bypass (no login screen):** `backend_dev.mjs` sets
  `SYNTHORG_DEV_AUTH_BYPASS=true`, which makes the backend expose the gated,
  password-free `POST /auth/dev-login`; the Vite frontend (`web/.env`'s
  `VITE_DEV_AUTH_BYPASS=true`) calls it on load and gets a REAL admin session
  (backend auth stays fully enforced -- only this one endpoint is gated). An
  admin account must already exist; if none does (fresh DB), the normal login /
  account-setup screen shows. The flag is DEV-ONLY and never set in production.
- **Auth across backend restarts:** the local backend reuses the container's
  stable `SYNTHORG_JWT_SECRET` and the shared Postgres, so a valid token's
  signature still verifies after a restart. Two hardening fixes keep you logged
  in across restarts: (1) `backend_dev.mjs` sets
  `SYNTHORG_API_JWT_EXPIRY_MINUTES=1440` (24h access token) so it never expires
  mid-session; (2) the SPA's bootstrap session check (`stores/auth.ts`
  `checkSession`) retries a genuine network error a few times instead of
  bouncing to login, so the ~3-5s restart window is ridden out. A real 401
  (true expiry) still logs out, as it should.
- Windows: the backend MUST run via `scripts/dev/run_api.py` (SelectorEventLoop)
  or psycopg's async pool fails on the Proactor loop.
- Vendor-neutral: this is local-only tooling; it touches no provider secrets
  beyond what `docker inspect` overlays into the child process.
