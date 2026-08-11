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
execute agent tools, never both. The execution topology matches the operator
arm: same stack, same database, same secrets, same daemon. What the overlay
changes is deliberate and development-only, and it is more than the source
mount: the backend is BUILT from this worktree instead of pulled at a verified
digest, `src/` is mounted read-only over the image's copy, publishing is
narrowed to loopback, `SYNTHORG_DEV_AUTH_BYPASS` opens a password-free
admin login on that loopback port, `SYNTHORG_API_JWT_EXPIRY_MINUTES` supplies a
longer session fallback, and bytecode is redirected to its own volume. Read
`docker/compose.dev.yml`, which states the reason for each.

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
   and that the stack is up. Ask for the compose SERVICES, never for container
   names: `--data-dir` and `SYNTHORG_STACK_PROJECT` both rename the project, so
   a valid stack need not contain `data-backend-1` at all and matching on that
   name would tell the user to start a stack that is already running.

   ```bash
   docker ps --filter 'label=com.docker.compose.service' \
     --format '{{.Names}} {{index .Labels "com.docker.compose.project"}} {{index .Labels "com.docker.compose.service"}}'
   ```

   One project should carry `postgres`, `nats` and `backend`. If none does, tell
   the user to run `synthorg start` (do not start it silently). If several do,
   name the one to overlay via `SYNTHORG_STACK_CONTAINER`, which is the same
   container `make dev-up` reads the project and compose files from: pass the
   container NAME from the first column of that `backend` row, which is why the
   command prints it.

2. **Bring the backend up from this worktree**: `make dev-up`. The first run
   builds the image (minutes); afterwards the venv layers are cached, so a
   dependency-free rebuild is much shorter. It is not free, though: `dev-up`
   passes `--build`, and the Dockerfile still `COPY`s `src/`, so a source edit
   invalidates that layer and reruns every step after it. The mount is what
   makes `make dev-restart` free, and that is the target for a source-only
   change. The target waits for `/api/v1/healthz`, opens a dev session against
   the loopback port, and reads the `agent_tool_execution` subsystem phase
   through it.

3. **Read that subsystem line.** `active` means this arm can spawn a subprocess
   and reach the container backend, and it is the only phase the target
   accepts: anything else exits non-zero, carrying the report with it. That is
   a verdict on capability rather than on the stack, which is up either way,
   and a run started in that state cannot mint a `CodeExecutionRecord`.
   `blocked` carries the condition the activation declined on, so surface that
   condition to the user rather than the exit status alone.

4. **Launch the frontend** (background) on port 3000 so existing bookmarks and
   the backend's expected origin match, with full logs. Create the log's parent
   first (`mkdir -p C:/tmp`): a fresh Windows host has no `C:/tmp`, and the
   launch then fails opening a log rather than starting Vite. Then, in the Bash
   tool's background mode:

   ```bash
   bash -o pipefail -c 'cd web && npm run dev -- --port 3000 --strictPort 2>&1 | tee C:/tmp/synthorg-dev-server.log'
   ```

   The `tee` is not decoration: step 5 reports that log path and `--status`
   tails it, so a launch that only starts Vite leaves both pointing at a file
   that never appears. It is also how the log gets written without a shell
   redirect creating a file, which the repository forbids. `-o pipefail` is
   what keeps that honest: a pipeline reports its LAST command, and `tee`
   succeeds whenever it can write the log, so without it a Vite that died on
   an already-busy `--strictPort` reads back as a successful launch. Wait for
   Vite "ready".

5. **Report.** Print `http://localhost:3000`, both log locations, and the
   one-line rule: web changes hot-reload; a Python change needs
   `make dev-restart`; a dependency change needs `make dev-up`.

## Sub-commands

- `--restart-backend`: `make dev-restart`. Recreates the backend container
  against the mounted source. Nothing rebuilds. The frontend keeps running.
- `--status`: `make dev-status`, which fails when `agent_tool_execution` is not
  `active`, plus whether local :3000 responds, plus a tail of the Vite log.
- `--stop`: kill the Vite dev server, and `make dev-down` to put the operator's
  own digest-pinned backend back.

## Notes

- **Logs are the contract.** The backend's are `make dev-logs`, which resolves
  the container from the running stack rather than a fixed name (`--data-dir`
  and `SYNTHORG_STACK_PROJECT` both rename it); the frontend's are
  `C:/tmp/synthorg-dev-server.log`. Surface both so the user sees every error.
- **Auth bypass (no login screen):** the overlay sets
  `SYNTHORG_DEV_AUTH_BYPASS=true`, which exposes the gated, password-free
  `POST /auth/dev-login`; the Vite frontend (`web/.env`'s
  `VITE_DEV_AUTH_BYPASS=true`) calls it on load and gets a REAL admin session
  (backend auth stays fully enforced; only this one endpoint is gated). An
  admin account must already exist. Never set in a production deployment.
- **Auth across restarts:** the arm keeps the stack's own `SYNTHORG_JWT_SECRET`
  and database, so a token still verifies after a restart. The SPA's bootstrap
  session check (`stores/auth.ts` `checkSession`) retries a genuine network
  error rather than bouncing to login, so the restart window is ridden out. The
  overlay also sets `SYNTHORG_API_JWT_EXPIRY_MINUTES=1440`, but that is an
  environment fallback: `api.jwt_expiry_minutes` follows the usual
  `DB > env > default` precedence, so a stack that already has the setting
  stored keeps its own expiry and a session can still lapse mid-iteration.
  Raise it in the dashboard if that bites.
- **The worktree is mounted read-only.** The container reads your source and can
  never write to it. Bytecode goes to a `/pycache` volume via
  `PYTHONPYCACHEPREFIX`, which also keeps any `__pycache__` your local `pytest`
  run left behind out of the container.
- Vendor-neutral: local-only tooling; it touches no provider secrets, and reads
  none into the transcript.
