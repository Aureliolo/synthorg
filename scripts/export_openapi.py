"""Export the Litestar OpenAPI schema and generate a standalone Scalar UI page.

Used by CI (pages.yml, pages-preview.yml) to generate:
- ``docs/openapi/openapi.json`` -- raw OpenAPI schema
- ``docs/openapi/reference.html`` -- standalone Scalar UI page

Both are written as siblings of ``docs/openapi/index.md`` (the REST API
landing page) so the docs build copies them into ``_site/docs/openapi/``
as static assets alongside the rendered landing page.

Can also be run locally before ``zensical build`` to preview the
API reference page (see Quick Commands in CLAUDE.md).
"""

import json
import os
import sys
import traceback
from pathlib import Path

# Repository root is the parent of the scripts/ directory
REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "docs" / "openapi"
SCHEMA_FILE = OUTPUT_DIR / "openapi.json"
HTML_FILE = OUTPUT_DIR / "reference.html"

# Deterministic export: without a persistence backend the app skips
# integrations / OAuth / tunnel / webhook / connection controllers,
# producing a partial schema that differs between environments.  Using an
# in-memory SQLite fixture wires the full public API surface while
# remaining ephemeral.  If the operator has not pinned a backend, force
# the in-memory SQLite contract AND strip ``SYNTHORG_DATABASE_URL`` so
# a pre-set Postgres URL cannot silently steer the export onto a
# different wiring path.  Explicit operator overrides (both env vars
# set together) are respected.
if "SYNTHORG_DB_PATH" not in os.environ:
    os.environ["SYNTHORG_DB_PATH"] = ":memory:"
    os.environ.pop("SYNTHORG_DATABASE_URL", None)

# Cursor-secret boot guard parity: synthorg.api.app.create_app refuses
# to start with an ephemeral pagination cursor secret -- dev, pre-release,
# and prod all share the same posture.  Build-time schema export must
# supply a stable value too, so pre-import setdefault keeps this script
# usable in CI and local previews without requiring the operator to set
# the env var explicitly.  Real deployments inject their own secret via
# the compose env block.
os.environ.setdefault(
    "SYNTHORG_PAGINATION_CURSOR_SECRET",
    "openapi-export-stable-cursor-secret-not-a-real-secret",
)

# Pinned for stability; update after testing newer releases in local preview.
# When bumping SCALAR_VERSION, recompute SCALAR_SRI:
#   curl -sL "https://cdn.jsdelivr.net/npm/@scalar/api-reference@<VERSION>" \
#     | openssl dgst -sha384 -binary | openssl base64 -A
SCALAR_VERSION = "1.48.5"
SCALAR_SRI = "sha384-l6LAVhtmxWg9mKRgVbB+DB/wFX+V9aQie5dzkMMvz+f4TO2B/0vRuSFsOdZraKZC"

STANDALONE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SynthOrg REST API Reference -- Interactive OpenAPI Schema</title>
  <meta name="description" content="Interactive reference for every SynthOrg REST API endpoint, request body, and response schema. Rendered from the OpenAPI 3.1 spec via Scalar.">
  <link rel="canonical" href="https://synthorg.io/docs/openapi/reference.html">
  <link rel="icon" href="../assets/images/favicon.png">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      clip-path: inset(50%);
      white-space: nowrap;
      border: 0;
    }
    .banner {
      padding: 0.6rem 1.5rem;
      font-size: 0.85rem;
      color: #555;
      background: #f8f9fa;
      border-bottom: 1px solid #e0e0e0;
      text-align: center;
    }
    .banner a {
      color: #1a73e8;
      text-decoration: none;
      margin-left: 1rem;
    }
    .banner a:hover { text-decoration: underline; }
    .banner code {
      background: rgba(0,0,0,0.06);
      padding: 0.1rem 0.4rem;
      border-radius: 0.2rem;
      font-size: 0.8rem;
    }
    @media (prefers-color-scheme: dark) {
      .banner { background: #1e1e1e; color: #aaa; border-color: #333; }
      .banner a { color: #8ab4f8; }
      .banner code { background: rgba(255,255,255,0.1); }
    }
  </style>
</head>
<body>
  <!-- Visually-hidden h1 (do not remove): gives crawlers + screen readers
       a single semantic heading for this page. The Scalar UI below renders
       the actual content via JavaScript and does not emit its own h1. -->
  <h1 class="sr-only">SynthOrg REST API Reference</h1>
  <div class="banner">
    Static snapshot of the OpenAPI schema;
    when running locally, use the live docs at <code>/docs/api</code> instead.
    <a href="./">&larr; Back to overview</a>
  </div>

  <script
    id="api-reference"
    data-url="openapi.json"
    data-configuration='{"theme": "default", "layout": "modern"}'
  ></script>
  <script
    src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@SCALAR_VERSION"
    integrity="SCALAR_SRI"
    crossorigin="anonymous"
  ></script>
  <noscript>
    <p style="padding: 2rem; text-align: center;">
      This API reference requires JavaScript to render.
      You can view the raw <a href="openapi.json">OpenAPI schema</a> instead.
    </p>
  </noscript>
</body>
</html>
""".replace("SCALAR_VERSION", SCALAR_VERSION).replace("SCALAR_SRI", SCALAR_SRI)


def main() -> int:
    """Instantiate the app, extract the OpenAPI schema, and write files."""
    try:
        from synthorg.api.app import create_app
        from synthorg.api.openapi import inject_rfc9457_responses

        app = create_app()
        schema_dict = app.openapi_schema.to_schema()
        schema_dict = inject_rfc9457_responses(schema_dict)
        schema_json = json.dumps(schema_dict, indent=2, ensure_ascii=False) + "\n"
    except Exception as exc:
        print("Failed to export OpenAPI schema:", file=sys.stderr)
        traceback.print_exception(exc)
        return 1

    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        SCHEMA_FILE.write_text(schema_json, encoding="utf-8")
        print(f"Wrote OpenAPI schema to {SCHEMA_FILE.relative_to(REPO_ROOT)}")

        HTML_FILE.write_text(STANDALONE_HTML, encoding="utf-8")
        print(f"Wrote Scalar UI page to {HTML_FILE.relative_to(REPO_ROOT)}")
    except OSError as exc:
        print("Failed to write output files:", file=sys.stderr)
        traceback.print_exception(exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
