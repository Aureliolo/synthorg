// Runs the SynthOrg API locally (uvicorn, app factory) on 127.0.0.1:3001
// against the already-running container Postgres + NATS, reusing the stopped
// backend container's env so secrets / encryption keys match the existing DB
// and admin session. Container env is read via `docker inspect` at launch so
// secrets never land in a transcript. All stdout/stderr is mirrored to the
// console AND a stable log file so backend errors are checkable in one place.
//
// Usage: node scripts/dev/backend_dev.mjs [logPath]
// Env overrides:
//   SYNTHORG_DEV_BACKEND_CONTAINER  (default: data-backend-1)
//   SYNTHORG_DEV_PG_HOSTPORT        (default: localhost:3002)  published Postgres
//   SYNTHORG_DEV_NATS_HOSTPORT      (default: localhost:3003)  published NATS
import { spawn, execFileSync } from 'node:child_process'
import { createWriteStream, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

const CONTAINER = process.env.SYNTHORG_DEV_BACKEND_CONTAINER ?? 'data-backend-1'
const PG_HOSTPORT = process.env.SYNTHORG_DEV_PG_HOSTPORT ?? 'localhost:3002'
const NATS_HOSTPORT = process.env.SYNTHORG_DEV_NATS_HOSTPORT ?? 'localhost:3003'
const runApi = fileURLToPath(new URL('run_api.py', import.meta.url))
const logPath = process.argv[2] ?? join(tmpdir(), 'synthorg-backend.log')
const out = createWriteStream(logPath, { flags: 'w' })

const localDirs = {
  SYNTHORG_LOG_DIR: join(tmpdir(), 'synthorg-local', 'logs'),
  SYNTHORG_MEMORY_DIR: join(tmpdir(), 'synthorg-local', 'memory'),
  // The container env carries a Postgres URL, so without an explicit artifact
  // dir the backend derives the agent workspace root as the Docker volume path
  // `/data/agent-workspaces` -- not an absolute path on native Windows (it
  // resolves to drive-relative `\data\...`), which fails the runtime-services
  // rebuild on `/setup/complete` with `workspace must be an absolute path`.
  // Pin it to a Windows-absolute temp dir, matching the LOG/MEMORY overrides.
  SYNTHORG_ARTIFACT_DIR: join(tmpdir(), 'synthorg-local', 'data'),
}
for (const dir of Object.values(localDirs)) mkdirSync(dir, { recursive: true })

// Pull the container's env (works on a stopped container) and keep only the
// app-relevant keys; the container's Linux PATH / cert paths must NOT leak in.
const raw = JSON.parse(
  execFileSync('docker', ['inspect', CONTAINER, '--format', '{{json .Config.Env}}'], {
    encoding: 'utf8',
  }),
)
const overlay = {}
for (const entry of raw) {
  const eq = entry.indexOf('=')
  if (eq === -1) continue
  const key = entry.slice(0, eq)
  if (!/^(SYNTHORG_|UVICORN_|MEM0_)/.test(key)) continue // drop PATH/SSL_CERT_FILE/PYTHON*
  overlay[key] = entry.slice(eq + 1)
}

// Repoint container-network hostnames at the published localhost ports.
if (overlay.SYNTHORG_DATABASE_URL) {
  overlay.SYNTHORG_DATABASE_URL = overlay.SYNTHORG_DATABASE_URL.replace(
    '@postgres:5432',
    `@${PG_HOSTPORT}`,
  )
}
if (overlay.SYNTHORG_NATS_URL) {
  overlay.SYNTHORG_NATS_URL = overlay.SYNTHORG_NATS_URL.replace('nats:4222', NATS_HOSTPORT)
}
Object.assign(overlay, localDirs)
overlay.SYNTHORG_HOST = '127.0.0.1'
overlay.UVICORN_HOST = '127.0.0.1'
overlay.SYNTHORG_PORT = '3001'
overlay.UVICORN_PORT = '3001'
overlay.PYTHONUNBUFFERED = '1'
// Long-lived (24h) access token for the dev loop so a backend restart never
// expires the session mid-iteration. Resolves via DB > env; the dev DB leaves
// this unset, so the env wins. Stays below the 7-day refresh-token lifetime
// (the AuthConfig validator requires refresh > access).
overlay.SYNTHORG_API_JWT_EXPIRY_MINUTES = '1440'

const env = { ...process.env, ...overlay }

out.write(
  `[backend-dev] starting uvicorn on 127.0.0.1:3001 (container=${CONTAINER})\n` +
    `[backend-dev] DB=${overlay.SYNTHORG_DATABASE_URL?.replace(/:\/\/[^@]*@/, '://***@')}\n` +
    `[backend-dev] NATS=${overlay.SYNTHORG_NATS_URL}\n`,
)

const child = spawn('uv', ['run', 'python', runApi], { shell: true, env })

const mirror = (stream) =>
  stream.on('data', (chunk) => {
    process.stdout.write(chunk)
    out.write(chunk)
  })
mirror(child.stdout)
mirror(child.stderr)

child.on('exit', (code) => {
  out.write(`\n[backend-dev] uvicorn exited with code ${code}\n`)
  out.end()
  process.exit(code ?? 0)
})

const forward = (sig) => process.on(sig, () => child.kill(sig))
forward('SIGTERM')
forward('SIGINT')
