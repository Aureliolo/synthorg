# User Guide

How to run SynthOrg.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose

## Quick Start (CLI)

The recommended way to run SynthOrg is via the CLI:

```bash
# Install CLI (Linux/macOS)
curl -sSfL https://synthorg.io/get/install.sh | bash

# Set up and start
synthorg init     # Interactive setup wizard
synthorg start    # Pull images + start containers
synthorg status   # Show container health and versions
```

The web dashboard is at [http://localhost:3000](http://localhost:3000) (default port).

Other CLI commands: `synthorg stop`, `synthorg logs`, `synthorg update`, `synthorg doctor`, `synthorg uninstall`.

## Quick Start (Docker Compose — manual)

For development or if you prefer manual Docker Compose:

```bash
git clone https://github.com/Aureliolo/synthorg
cd synthorg
cp docker/.env.example docker/.env
docker compose -f docker/compose.yml up -d
```

### Containers

| Container | Image | Description |
|-----------|-------|-------------|
| **backend** | `ghcr.io/aureliolo/synthorg` | Python API server (Litestar). 3-stage build, Chainguard distroless runtime (no shell), runs as non-root (UID 65532). |
| **web** | `ghcr.io/aureliolo/synthorg-web` | Nginx + Vue 3 dashboard (PrimeVue + Tailwind CSS). SPA routing, proxies API and WebSocket requests to backend. |

### Environment Variables

Configuration is in `docker/.env` (copy from `docker/.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNTHORG_JWT_SECRET` | *(auto-generated)* | JWT signing secret. Auto-generated and persisted on first run. Set explicitly only for multi-instance deployments. Must be >= 32 characters if set. |
| `SYNTHORG_DB_PATH` | `/data/synthorg.db` | SQLite database path (inside container). |
| `SYNTHORG_MEMORY_DIR` | `/data/memory` | Agent memory storage directory (inside container). |
| `BACKEND_PORT` | `8000` | Host port for the backend API. |
| `WEB_PORT` | `3000` | Host port for the web dashboard. |
| `DOCKER_HOST` | *(unset)* | Docker socket for agent code execution sandbox (optional). |

### First-Run Setup

After the containers are running:

1. **Create an admin account** by sending a POST request to the setup endpoint:

    ```bash
    curl -X POST http://localhost:8000/api/v1/auth/setup \
      -H "Content-Type: application/json" \
      -d '{"username": "admin", "password": "your-secure-password"}'
    ```

2. **Access the dashboard** at [http://localhost:3000](http://localhost:3000) and log in with your admin credentials.

3. **Verify health** with `curl http://localhost:8000/api/v1/health`.

Organization setup (choosing templates, configuring agents) is done via the dashboard. Custom template editing through the UI is planned for a future release.

!!! info "Active Development"
    SynthOrg is under active development. The web dashboard is available for monitoring and managing the organization. Templates and some features described here may evolve. Check the [GitHub repository](https://github.com/Aureliolo/synthorg) for current status.

## Templates

Choose a pre-built organization template to get started quickly:

| Template | Description |
|----------|-------------|
| `startup` | CEO + small engineering team |
| `agency` | Project manager + specialists |
| `research-lab` | Lead researcher + research assistants |

Templates are selected through the dashboard. Full list coming soon.

## Stop

```bash
docker compose -f docker/compose.yml down
```

## Next Steps

- Templates — Full list of pre-built configurations (coming soon)
- REST API — Interact with your org via the API (coming soon)
- [Design Specification](design/index.md) — Full architecture details
