# User Guide

How to run SynthOrg.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose

## Quick Start (CLI)

The recommended way to run SynthOrg is via the CLI:

```bash
# Install CLI (Linux/macOS)
curl -sSfL https://raw.githubusercontent.com/Aureliolo/synthorg/main/cli/scripts/install.sh | sh

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

Container configuration (ports, storage paths, log level) is defined in `docker/.env`. Organization setup is done via the dashboard. Custom template editing through the UI is planned for a future release.

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
