# User Guide

How to run SynthOrg.

SynthOrg takes a description of a piece of software and builds it in one pass: the work is
split into a tree of parts, the parts are built in parallel in isolated containers, and each
one is checked by something that did not write it. It runs on your hardware, against the
providers and models you configure, and your code does not leave your machine.

!!! warning "Pre-alpha"

    The loop has been driven live against a real deployment twelve times and has never
    reached the assembly stage: no run has produced an assembled deliverable, and no
    completion has been recorded. The platform, CLI, dashboard and setup wizard below are
    built and do what this page describes, and every action the system takes runs under the
    oversight mode you set. Nothing here promises you working software. See the
    [Roadmap](roadmap/index.md) for what is wired versus what is intent.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose

## Quick Start (CLI)

The CLI is a Docker orchestrator: it generates the compose stack, verifies images, and
starts and stops the containers. Everything else happens in the dashboard and the REST API.

```bash
# Install CLI (Linux/macOS)
curl -sSfL https://synthorg.io/get/install.sh | bash

# Set up and start
synthorg init     # Interactive setup wizard
synthorg start    # Verify + pull images + start containers
synthorg status   # Show container health and versions
```

`synthorg start` (and `synthorg update`) automatically verifies container image **cosign signatures** and **SLSA provenance** before pulling. If verification fails (for example in an air-gapped environment without access to Sigstore infrastructure), pass `--skip-verify` or set `SYNTHORG_SKIP_VERIFY=1`.

The web dashboard is at [http://localhost:3000](http://localhost:3000) (default port).

Other CLI commands: `synthorg stop`, `synthorg logs`, `synthorg update`, `synthorg doctor`, `synthorg uninstall`, `synthorg backup`, `synthorg worker`, `synthorg wipe`, `synthorg cleanup`, `synthorg config`, `synthorg completion-install`, `synthorg version`. When updating, the CLI re-launches itself after binary replacement so the remaining steps (compose refresh, image pull) use the new version. If the compose template has structural changes (new environment variables, hardening tweaks), the diff is shown for approval before applying; version comment and image reference updates are applied automatically.

To automatically clean up old container images after updates (keeping only the current and previous version), run `synthorg config set auto_cleanup true`. Use `synthorg config get <key>` to retrieve a single configuration value (for example `synthorg config get channel`).

To opt in to pre-release builds (dev channel), run `synthorg config set channel dev`. Dev channel builds are created on every push to main between stable releases and include Docker images, CLI binaries, cosign signatures, and SLSA provenance. To switch back: `synthorg config set channel stable`.

## Quick Start (manual Docker Compose)

`docker/compose.yml` is the source-build stack: it builds the backend image from the
repository rather than pulling a published one, so it is the path for development and for
running an unreleased tree. It needs three things the CLI would otherwise do for you.

1. Clone the repository and create `docker/.env` from the example:

    ```bash
    git clone https://github.com/Aureliolo/synthorg
    cd synthorg
    cp docker/.env.example docker/.env
    ```

2. Fill in the required secrets in `docker/.env`. Every one of them is commented out in the
   example, and the stack refuses to start without them:

    ```bash
    # each of these prints one value to paste into docker/.env
    python -c "import secrets; print(secrets.token_urlsafe(48))"   # SYNTHORG_JWT_SECRET
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # SYNTHORG_SETTINGS_KEY
    python -c "import secrets; print(secrets.token_urlsafe(32))"   # SYNTHORG_PAGINATION_CURSOR_SECRET
    python -c "import secrets; print(secrets.token_urlsafe(32))"   # POSTGRES_PASSWORD
    ```

3. Build and start, naming the runtime base image the backend Dockerfile layers on. There
   is deliberately no default: a build without `BASE_IMAGE` fails fast rather than pulling a
   mutable tag.

    ```bash
    BASE_IMAGE=ghcr.io/aureliolo/synthorg-backend-base:main \
      docker compose -f docker/compose.yml up -d --build
    ```

### Services

| Service | Image | Description |
|---------|-------|-------------|
| **data-init** | `busybox` (digest-pinned) | One-shot: sets ownership on the named volumes, which are root-owned on creation and so cannot be written to by the non-root images. Exits before the rest start. |
| **postgres** | `dhi.io/pgvector` (digest-pinned) | Operational data and memory embeddings; the pgvector variant supplies the vector extension. |
| **nats** | `dhi.io/nats` (digest-pinned) | Message bus for the distributed worker path. |
| **backend** | built from `docker/backend/Dockerfile` | Python API server (Litestar). Two-stage build onto a Wolfi apko-composed distroless runtime (no shell), runs as non-root (UID 65532). |
| **web** | `ghcr.io/aureliolo/synthorg-web` | Caddy + React 19 dashboard (shadcn/ui + Tailwind CSS). Pure apko Wolfi image, SPA routing, proxies API and WebSocket requests to the backend, serves the embedded documentation at `/docs/`. |

The stack the CLI generates pulls a published `ghcr.io/aureliolo/synthorg-backend` image
instead of building one, and is otherwise the same shape.

### Environment Variables

Configuration is in `docker/.env` (copy from `docker/.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNTHORG_JWT_SECRET` | *(required)* | JWT signing secret. Generated by `synthorg init`. Must be at least 32 characters. |
| `SYNTHORG_SETTINGS_KEY` | *(required)* | Fernet encryption key for sensitive settings at rest. Generated by `synthorg init`. Must be a valid Fernet key (32 bytes, URL-safe base64). |
| `SYNTHORG_PAGINATION_CURSOR_SECRET` | *(required)* | HMAC secret for signing pagination cursors. Generated by `synthorg init`. Must be at least 16 bytes; the backend refuses to start with an ephemeral per-process key, because rotating it across restarts silently invalidates every outstanding cursor token. |
| `POSTGRES_PASSWORD` | *(required)* | Password for the compose-managed Postgres service. Generated by `synthorg init`. Compose fails to start without it. |
| `SYNTHORG_MASTER_KEY` | *(required when secret encryption is enabled)* | Master key for envelope-encrypting stored secrets. Generated by `synthorg init` and written into the CLI-generated stack; not needed when `encrypt_secrets` is disabled. |
| `SYNTHORG_DB_PATH` | `/data/synthorg.db` | SQLite database path (inside the container). Only used by a SQLite install: the bundled compose stack sets `SYNTHORG_DATABASE_URL` for its managed Postgres, which takes precedence. |
| `SYNTHORG_MEMORY_DIR` | `/data/memory` | Agent memory storage directory (inside the container). |
| `SYNTHORG_LOG_DIR` | `/data/logs` | Log file directory (inside the container), persisted on a volume. |
| `SYNTHORG_PERSISTENCE_BACKEND` | `postgres` | Persistence backend for operational data. The bundled compose stack pins this on the backend service, which overrides the value here; the Python process chooses the backend from `SYNTHORG_DATABASE_URL` versus `SYNTHORG_DB_PATH`. |
| `SYNTHORG_MEMORY_BACKEND` | `sqlvector` | Memory backend for agent memory. `sqlvector` is durable and semantically searchable, `composite` routes per namespace, and `inmemory` is discouraged (substring matching, lost on restart). |
| `BACKEND_PORT` | `3001` | Host port for the backend API. |
| `WEB_PORT` | `3000` | Host port for the web dashboard. |
| `POSTGRES_PORT` | `3002` | Host port for Postgres. |
| `NATS_CLIENT_PORT` | `3003` | Host port for the NATS client connection. |
| `DOCKER_HOST` | *(unset)* | Docker socket for the agent code execution sandbox (optional). |

The block at the end of `docker/.env.example` holds the compose-set settings: values the
backend reads once when it starts and that nothing inside the running system can change, so
the dashboard shows them read-only and rejects a write. Everything else is live and belongs
in the dashboard rather than in this file.

### First-Run Setup

After the containers are running, open the web dashboard at [http://localhost:3000](http://localhost:3000). On a fresh install, the **setup wizard** appears automatically. On a fresh install without an admin account, you first create an admin user; you then choose a setup mode:

- **Guided Setup** (recommended): walks through every configuration step.
- **Quick Setup**: sets a company name, adds a provider, and completes. Everything else can be configured later in Settings.

**Guided Setup steps:**

1. **Account** (conditional): create the first admin user. This step only appears when no admin account exists yet.
2. **Mode**: choose **Guided Setup** (continues through every step below) or **Quick Setup** (the abbreviated provider, company, complete path).
3. **Template**: choose a template. Templates are displayed in a searchable grid with category and size filters, and grouped into Recommended and Other sections. Each card shows structural metadata (agent count, departments, autonomy level, workflow). Side-by-side comparison is available.
4. **Providers**: configure LLM providers. Local providers are auto-detected with a re-scan button; additional providers can be added via the full provider form supporting API key, subscription, and custom configurations. Model discovery runs automatically after adding a provider.
5. **Company**: name the deployment, set a description, choose a display currency, and select a model spend profile (Economy, Balanced, or Premium).
6. **Agents**: customise agent names and model assignments. Agents are pre-populated from the selected template with models matched to the providers you configured.
7. **Capabilities**: enable or disable optional platform capabilities, grouped by purpose. The two conversational groups render expanded with their on-by-default toggles front-and-centre; advanced groups (off by default) render collapsed. Each toggle explains its trade-off.
8. **Theme**: set UI preferences including colour palette, typography, layout density, animation level, and sidebar position.
9. **Complete**: review a summary of the configuration and finish setup. This stores the company and brings the platform up, with the roster staffed and every agent bound to the provider and model you chose for it.

The backend validates that a company and at least one provider exist before allowing setup to finish. Agents are optional (Quick Setup skips agent configuration). Steps are completed sequentially; a later step only appears done if all prior steps are also complete. Completed steps show a summary and can be revisited via the step indicator. After completing the wizard, the dashboard appears and the setup wizard is not shown again.

To start fresh, use `synthorg wipe` (offers an interactive backup, wipes all data, and optionally restarts with a clean slate to re-open the setup wizard) or delete the `api.setup_complete` setting via the settings API.

## Templates

A template is a starting roster and its budget split. It decides who is available to be
assigned work and who is available to review it. An agent may not review its own work, so a
roster with nobody else holding the reviewing role parks every finished task rather than
judging it; that is why the smallest shipped template is three agents and not two.

| Key | Name | Description |
|-----|------|-------------|
| `solo_founder` | Solo Builder | Lean three-agent setup: CEO, full-stack developer, and a completion reviewer who signs off finished work |
| `startup` | Tech Startup | Six-agent team oriented around shipping quickly |
| `dev_shop` | Engineering Squad | Lean engineering team built for throughput on a tight budget |
| `product_team` | Product Studio | Product organisation where UX research gathers requirements first |
| `agency` | Agency | Client-services organisation combining creative, marketing, and design |
| `full_company` | Enterprise Org | Full enterprise simulation spanning all departments with a C-suite |
| `research_lab` | Research Lab | Autonomous research organisation, analysis-first |
| `consultancy` | Consultancy | Senior-heavy, supervised client-facing advisory firm |
| `data_team` | Data Team | Data-first organisation focused on analytics and ML |
| `support_desk` | Support Desk | Customer-facing support and incident-response organisation |
| `security_team` | Security Team | Security-first: threat modelling, security review, and audit |
| `growth_marketing` | Growth Marketing Studio | Content and marketing studio for campaigns, copy, and analytics |

Templates are selected through the dashboard during the setup wizard.

## Stop

```bash
docker compose -f docker/compose.yml down
```

Or, for a CLI-managed stack, `synthorg stop`. Data persists in the named Docker volumes and
is available next time you start.

## Next Steps

- [Guides](guides/index.md): in-depth guides for configuration, agents, budgets, security, and more
- [REST API Reference](openapi/index.md): drive the platform over the API
- [Design Specification](design/index.md): full architecture details
