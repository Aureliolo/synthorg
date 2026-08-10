---
description: "Stand up the local dev arm: the backend built from this worktree running in the operator's own stack, plus a live-reload Vite frontend, with both logs captured"
argument-hint: "[--restart-backend] [--stop] [--status]"
allowed-tools:
  - Bash
  - PowerShell
  - Read
  - Write
  - BashOutput
---

# setup-live-iterative

Stand the local dev arm up: the **backend built from this worktree**, running in
the stack the operator is already running, plus a **Vite dev server** on the
host so web changes hot-reload. Postgres, NATS, every secret and the configured
organisation are untouched, because only the `backend` service is swapped.

Topology after setup:

```
browser :3000  ->  Vite dev server (web/, HMR)  --/api proxy-->  :3001
                                                                  |
                        backend CONTAINER: shipped Dockerfile, built from THIS
                        worktree, src/ bind-mounted read-only over /app/src
                                                                  |
                    postgres + nats (same stack, untouched)  +  docker.sock
                                                                  |
                                                  sandbox + sidecar containers
```

The backend runs where it ships rather than natively. That is not a preference:
on Windows psycopg's async pool requires the `SelectorEventLoop`, while both
`asyncio.create_subprocess_exec` and the Docker named pipe require the
`ProactorEventLoop`, so a native backend on Postgres can drive the database or
execute agent tools, never both (#2745). The dev arm and the operator arm now
differ in exactly one respect: whether `src/` is baked into the image or
mounted over it.

## What drives it

`make dev-up` and friends, in the repository root. They work out which compose
file made the running stack by asking the daemon, and derive the base image
from the running backend's own build label, so nothing here is a second copy of
a fact that lives somewhere else. Overrides: `SYNTHORG_STACK_CONTAINER`,
`SYNTHORG_STACK_PROJECT`, `SYNTHORG_BACKEND_BASE_IMAGE`.

The overlay is `docker/compose.dev.yml`.

## Procedure

Default invocation (no args) does the full bring-up:

1. **Preconditions.** Confirm a git repo (`git rev-parse --is-inside-work-tree`)
   and that the stack is up: `docker ps --format '{{.Names}}'` should list
   `data-postgres-1`, `data-nats-1` and `data-backend-1`. If it is not, tell the
   user to run `synthorg start` (do not start it silently).

2. **Bring the backend up from this worktree**: `make dev-up`. The first run
   builds the image (minutes); afterwards the venv layers are cached and a
   source-only change rebuilds nothing at all, because `src/` is mounted rather
   than copied. The target waits for `/api/v1/healthz` and then prints the
   `agent_tool_execution` subsystem line.

3. **Read that subsystem line.** `active` means this arm can spawn a subprocess
   and reach the container backend. `blocked` names the condition and what it
   costs, and a run started in that state cannot mint a `CodeExecutionRecord`.
   Surface it to the user rather than proceeding past it.

4. **Launch the frontend** (background) on port 3000 so existing bookmarks and
   the backend's expected origin match, with full logs:
   `bash -c "cd web && npm run dev -- --port 3000 --strictPort"` redirected to
   `C:/tmp/synthorg-dev-server.log` (use the Bash tool's background mode; do
   NOT use shell redirects to create the file: pass the log path to the process
   or tee within the launched command's own shell). Wait for Vite "ready".

5. **Report.** Print `http://localhost:3000`, both log locations, and the
   one-line rule: web changes hot-reload; a Python change needs
   `make dev-restart`; a dependency change needs `make dev-up`.

## Sub-commands

- `--restart-backend`: `make dev-restart`. Recreates the backend container
  against the mounted source. Nothing rebuilds. The frontend keeps running.
- `--status`: `make dev-status`, plus whether local :3000 responds, plus a tail
  of the Vite log.
- `--stop`: kill the Vite dev server, and `make dev-down` to put the operator's
  own digest-pinned backend back.

## Notes

- **Logs are the contract.** The backend's are `make dev-logs` (or
  `docker logs data-backend-1`); the frontend's are
  `C:/tmp/synthorg-dev-server.log`. Surface both so the user sees every error.
- **Auth bypass (no login screen):** the overlay sets
  `SYNTHORG_DEV_AUTH_BYPASS=true`, which exposes the gated, password-free
  `POST /auth/dev-login`; the Vite frontend (`web/.env`'s
  `VITE_DEV_AUTH_BYPASS=true`) calls it on load and gets a REAL admin session
  (backend auth stays fully enforced; only this one endpoint is gated). An
  admin account must already exist. Never set in a production deployment.
- **Auth across restarts:** the arm keeps the stack's own `SYNTHORG_JWT_SECRET`
  and database, so a token still verifies after a restart. The overlay sets
  `SYNTHORG_API_JWT_EXPIRY_MINUTES=1440` so a session never expires
  mid-iteration, and the SPA's bootstrap session check (`stores/auth.ts`
  `checkSession`) retries a genuine network error rather than bouncing to login,
  so the restart window is ridden out.
- **The worktree is mounted read-only.** The container reads your source and can
  never write to it. Bytecode goes to a `/pycache` volume via
  `PYTHONPYCACHEPREFIX`, which also keeps any `__pycache__` your local `pytest`
  run left behind out of the container.
- Vendor-neutral: local-only tooling; it touches no provider secrets, and reads
  none into the transcript.
