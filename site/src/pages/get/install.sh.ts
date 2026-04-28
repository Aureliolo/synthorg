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
import { resolve } from "node:path";

import type { APIRoute } from "astro";

export const prerender = true;

export const GET: APIRoute = () => {
  const scriptPath = resolve("../cli/scripts/install.sh");
  const content = readFileSync(scriptPath, "utf-8");
  return new Response(content, {
    headers: {
      "Content-Type": "application/x-sh; charset=utf-8",
      "Cache-Control": "public, max-age=300",
    },
  });
};
