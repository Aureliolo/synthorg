# User Guide

How to run SynthOrg.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose

## Quick Start

```bash
git clone https://github.com/Aureliolo/synthorg
cd synthorg
docker compose -f docker/compose.yml up -d
```

The web dashboard is at [http://localhost:3000](http://localhost:3000).

All configuration — LLM provider keys, organization setup, templates — is managed through the dashboard.

!!! danger "Work in Progress"
    SynthOrg is under active development. The web dashboard, templates, and many features described here are **not yet available**. Check the [GitHub repository](https://github.com/Aureliolo/synthorg) for current status.

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
- [Design Specification](design_spec.md) — Full architecture details
