"""Export the Litestar OpenAPI schema and generate a standalone Scalar UI page.

Used by CI (pages.yml, pages-preview.yml) to generate:
- ``docs/_generated/openapi.json`` — raw OpenAPI schema
- ``docs/_generated/api-reference.html`` — standalone Scalar UI page

Both are generated before the docs build so the docs site can
link to the interactive API reference.

Can also be run locally before ``zensical build`` to preview the
API reference page (see Quick Commands in CLAUDE.md).
"""

import json
import sys
import traceback
from pathlib import Path

# Repository root is the parent of the scripts/ directory
REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "docs" / "_generated"
SCHEMA_FILE = OUTPUT_DIR / "openapi.json"
HTML_FILE = OUTPUT_DIR / "api-reference.html"

# Pinned for stability; update after testing newer releases in local preview
SCALAR_VERSION = "1.48.5"

STANDALONE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>REST API Reference &mdash; SynthOrg</title>
  <link rel="icon" href="../assets/images/favicon.png">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
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
  <div class="banner">
    Static snapshot of the OpenAPI schema &mdash;
    when running locally, use the live docs at <code>/docs/api</code> instead.
    <a href="../">&larr; Back to docs</a>
  </div>

  <script
    id="api-reference"
    data-url="openapi.json"
    data-configuration='{"theme": "default", "layout": "modern"}'
  ></script>
  <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@SCALAR_VERSION"></script>
  <noscript>
    <p style="padding: 2rem; text-align: center;">
      This API reference requires JavaScript to render.
      You can view the raw <a href="openapi.json">OpenAPI schema</a> instead.
    </p>
  </noscript>
</body>
</html>
""".replace("SCALAR_VERSION", SCALAR_VERSION)


def main() -> int:
    """Instantiate the app, extract the OpenAPI schema, and write files."""
    try:
        from synthorg.api.app import create_app

        app = create_app()
        schema_dict = app.openapi_schema.to_schema()
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
