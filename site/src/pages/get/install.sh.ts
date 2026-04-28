/**
 * Static endpoint that serves ``cli/scripts/install.sh`` at
 * ``/get/install.sh``.
 *
 * The canonical install script lives in ``cli/scripts/`` so it can be
 * shipped with the Go CLI release.  This endpoint reads it at build
 * time and emits ``dist/get/install.sh`` so the marketing site's
 * download link and the ``curl | bash`` quickstart resolve to the
 * exact same bytes; placing a duplicate copy under ``site/public/``
 * would create a drift hazard.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { APIRoute } from "astro";

export const prerender = true;

// Resolve the canonical script path relative to THIS source file rather
// than ``process.cwd()``: the cwd at build time depends on where ``astro
// build`` is invoked from (repo root, ``site/``, monorepo runner, etc.)
// and the script breaks silently when it differs.  Anchoring to
// ``import.meta.url`` keeps the path stable across all invocation
// surfaces.
const SCRIPT_PATH = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../cli/scripts/install.sh",
);

export const GET: APIRoute = () => {
  const content = readFileSync(SCRIPT_PATH, "utf-8");
  return new Response(content, {
    headers: {
      "Content-Type": "application/x-sh; charset=utf-8",
      "Cache-Control": "public, max-age=300",
    },
  });
};
