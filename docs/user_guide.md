# User Guide

How to install and run SynthOrg.

!!! warning "Under Construction"
    SynthOrg is under active development. Some features described here may not be available yet.

## Install

```bash
pip install synthorg
```

Or with uv:

```bash
uv add synthorg
```

## Configure

Define your organization in a YAML file:

```yaml
# company.yaml
company:
  name: "My AI Company"

agents:
  - role: ceo
    name: "Chief Executive"
    model: large

  - role: engineer
    name: "Backend Engineer"
    model: medium
    tools:
      - file_system
      - code_runner
```

## Run

```python
import asyncio

from ai_company.config.loader import load_config
from ai_company.engine.agent_engine import AgentEngine

async def main():
    config = load_config("company.yaml")
    engine = AgentEngine(config)
    result = await engine.run("Build a REST API for user management")
    print(result)

asyncio.run(main())
```

## Run with Docker

```bash
cp docker/.env.example docker/.env   # configure env vars
docker compose -f docker/compose.yml up -d
```

```bash
curl http://localhost:8000/api/v1/health
```

## Next Steps

- [Configuration Reference](#) — Full YAML schema (coming soon)
- [Templates](#) — Pre-built company configurations (coming soon)
- [Design Specification](https://github.com/Aureliolo/synthorg/blob/main/DESIGN_SPEC.md) — Full architecture details
