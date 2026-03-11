# ADR-003: Documentation & Site Architecture

- **Status**: Accepted
- **Date**: 2026-03-11
- **Decision Makers**: Aurelio

## Context

SynthOrg needs a public-facing website with:

1. A landing page that communicates the project's value proposition
2. Auto-generated API reference documentation from Python docstrings
3. Architecture documentation and guides
4. The same docs available inside the Vue 3 web dashboard at `/docs`

Key constraints:
- Python 3.14+ project with Google-style docstrings and Pydantic v2 models
- Docs must stay in sync with code (auto-generated API reference)
- Minimal maintenance overhead
- Custom domain (`synthorg.io`) already purchased

## Options Considered

### Documentation Engine

| Option | Python API Docs | Landing Page | Vue 3 Integration |
|--------|----------------|-------------|-------------------|
| **MkDocs + Material + mkdocstrings** | Excellent (Griffe AST) | Good (custom overrides) | Good (static embed) |
| Sphinx + sphinx-immaterial | Excellent (autodoc) | Poor | Poor |
| VitePress | Poor (no Python) | Good | Excellent (native Vue) |
| Docusaurus | Poor (no Python) | Excellent | Poor (React) |
| Starlight (Astro) | Poor (no Python) | Excellent | Fair |

### Landing Page SSG

| Option | Interactivity | Performance | Ecosystem |
|--------|--------------|-------------|-----------|
| **Astro** | Islands architecture | Best (zero JS default) | 48k stars, active |
| Next.js | Full React | Good | Most popular |
| Plain HTML + Tailwind | Manual JS | Fastest | No framework |

### Docs Sharing (Public Site ↔ Web Dashboard)

| Approach | Maintenance | UX Quality | API Docs |
|----------|------------|------------|----------|
| **Build output embedding** | Minimal | Good (sub-site) | Works perfectly |
| Shared markdown + dual renderers | High | Best | Broken (mkdocstrings directives) |
| Iframe embedding | Minimal | Poor (double scrollbars) | Works |
| VitePress for both | Medium | Best | Needs griffe2md |
| Micro-frontend | High | Good | Over-engineered |

### Domain Structure

| Option | SEO | Maintenance | Flexibility |
|--------|-----|------------|-------------|
| **Single domain, multi-tool CI merge** | Best | Medium | Best of both tools |
| Subdomain split | Good | Higher (2 repos) | Full independence |
| Single domain, single tool | Best | Lowest | Compromised quality |

## Decision

1. **MkDocs + Material + mkdocstrings** for documentation
   - Griffe AST-based extraction (PEP 649 safe, no runtime imports)
   - Griffe Pydantic extension for model field documentation
   - Google-style docstring support (native)
   - Same toolchain as Pydantic, FastAPI, LiteLLM

2. **Astro** for landing page (Concept C: Hybrid)
   - Zero JS by default, islands for interactive components
   - Dark-to-light-to-dark gradient, vivid violet + teal palette
   - Provocative hero: "What if your company had infinite, tireless employees?"
   - Scroll-synced code panel, expandable architecture (future)

3. **Build output embedding** for docs in Vue dashboard
   - `mkdocs build` → copy static HTML into Vue app's `public/docs/`
   - Nginx serves at `/docs/` with location block
   - Same HTML in both locations, zero rendering discrepancies

4. **Single domain** (`synthorg.io`) with multi-tool CI merge
   - Astro landing page at `/`
   - MkDocs docs at `/docs/`
   - Single GitHub Pages deployment
   - CI merges both build outputs into one artifact

5. **Same repo** — all in `Aureliolo/synthorg`
   - `docs/` for MkDocs markdown source
   - `site/` for Astro landing page source
   - `mkdocs.yml` at repo root
   - Docs always match code version

## Documentation Content

- **Getting Started / Guides**: Hand-written tutorials (deferred)
- **Python API Reference**: Auto-generated from docstrings via mkdocstrings (implemented)
- **Architecture / Design Decisions**: ADRs + architecture overview (implemented)
- **REST API Docs**: Link to running Scalar instance (Litestar built-in); static OpenAPI render deferred

## Consequences

### Positive

- API docs auto-update on every push — always in sync with code
- Landing page and docs use best-in-class tools for their respective jobs
- Single domain simplifies DNS and maximizes SEO
- Python-native docs toolchain (no Node.js needed for docs)

### Negative

- Two build tools in CI (Python + Node.js) — slightly more complex pipeline
- Landing page changes trigger full CI (mitigated by path filters)
- MkDocs Material entering maintenance mode (mitigated by Zensical migration path)
- Docs in Vue dashboard feel like a sub-site (accepted trade-off)

### Neutral

- Astro landing page scaffold is minimal — content development is deferred
- MkDocs overrides directory reserved for future custom landing page within docs

## Implementation

- `mkdocs.yml` — MkDocs configuration (nav structure updated for design spec split)
- `docs/` — documentation source:
    - `docs/design/` — 7 design spec pages (split from monolithic `DESIGN_SPEC.md`)
    - `docs/architecture/` — overview, tech stack, decision log
    - `docs/roadmap/` — status, open questions, future vision
    - `docs/reference/` — research and industry standards
    - `docs/api/` — auto-generated API reference
    - `docs/decisions/` — ADR files (detailed analysis)
- `DESIGN_SPEC.md` — pointer file linking to the 7 design pages
- `site/` — Astro landing page source
- `.github/workflows/pages.yml` — multi-tool CI merge + GitHub Pages deployment
- `pyproject.toml` — `docs` dependency group added
