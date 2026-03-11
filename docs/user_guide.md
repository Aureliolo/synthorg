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

Pick a pre-built template or define your own organization:

=== "Use a template"

    ```python
    from ai_company.templates import load_template

    config = load_template("startup")  # startup, agency, research-lab, etc.
    ```

    See [Templates](#) for the full list of available configurations.

=== "Custom YAML"

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

from ai_company.engine.agent_engine import AgentEngine

async def main():
    engine = AgentEngine(config)  # from template or load_config("company.yaml")
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
