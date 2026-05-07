# `data/` -- canonical build-time inputs

Files in this directory are the single source of truth for content
generated into the public docs. They are committed: pull requests show
drift in the diff.

## Files

| File | Consumers | Refreshed by |
|------|-----------|--------------|
| `competitors.yaml` | `scripts/generate_comparison.py`, `site/src/pages/compare.astro` | Hand-edited; `last_updated` may be `auto` to track git history |
| `runtime_stats.yaml` | `scripts/inject_runtime_stats.py` | `scripts/generate_runtime_stats.py` (run by CI before `zensical build`) |

## `runtime_stats.yaml`

Numeric claims that would otherwise rot in the docs (test count, latest
release tag, Mem0 star count, provider preset count, subagent count)
live here as `stats.<name>.display` strings. The injector substitutes
each marker into rendered docs:

```text
Source markdown:   <!--RS:tests-->OLD<!--/RS--> tests
After 1st inject:  <!--RS:tests-->27,000+<!--/RS--> tests
After 2nd inject:  <!--RS:tests-->27,000+<!--/RS--> tests   (idempotent: identical to 1st)
Rendered HTML:     27,000+ tests   (HTML comments stripped by markdown)
```

Idempotency means re-running the injector on the same YAML produces
identical output. If the generator refreshes the YAML between runs,
the next inject will pick up the new value cleanly.

The generator is offline-tolerant. When `pytest --collect-only`,
`gh release list`, `gh api`, or any other source call fails, the
generator logs a structured WARNING and preserves the existing value
in this file rather than overwriting with a placeholder.

## Refresh locally

```bash
uv run python scripts/generate_runtime_stats.py
uv run python scripts/inject_runtime_stats.py
```

The first script rewrites `data/runtime_stats.yaml`. The second rewrites
the markers in `README.md` and the docs files in scope.

## Schema

```yaml
schema_version: 1                       # bump when field shapes change
last_generated_utc: "2026-05-06T00:00:00Z"
generator_revision: "<git sha>"

stats:
  <name>:
    raw: <int|str>                      # exact value as fetched
    rounded: <int>                      # optional; floor to a named step
    display: "<string>"                 # what the injector substitutes

sources:
  <name>: "<short description>"         # human pointer to the input
```

## Adding a new stat

1. Pick a snake-case `name` and add the entry to `stats:` with seed
   values.
2. Add a fetcher under `_FETCHERS` in
   `scripts/generate_runtime_stats.py` and a matching entry in the
   module's `_SOURCES` mapping.
3. Wrap any existing literal in `README.md` or `docs/` in
   `<!--RS:name-->value<!--/RS-->` markers.
4. Run the gate (`scripts/check_doc_numeric_macros.py`) to confirm no
   bare literals remain. The gate scans for any digit adjacent to known
   stat nouns and fails if it isn't wrapped.
