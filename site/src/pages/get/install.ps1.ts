/**
 * Static endpoint that serves ``cli/scripts/install.ps1`` at
 * ``/get/install.ps1``.
 *
 * Mirror of ``install.sh.ts`` for Windows / PowerShell.  Reads the
 * canonical PowerShell installer from ``cli/scripts/`` at build time
 * so the download link and the ``iwr | iex`` quickstart resolve to
 * the same bytes.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { APIRoute } from "astro";

export const prerender = true;

// See install.sh.ts for the rationale: anchoring to
// ``import.meta.url`` avoids cwd-dependent path resolution that
// would otherwise be brittle in monorepos.
const SCRIPT_PATH = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../cli/scripts/install.ps1",
);

export const GET: APIRoute = () => {
  const content = readFileSync(SCRIPT_PATH, "utf-8");
  return new Response(content, {
    headers: {
      // PowerShell scripts are typically served as text/plain.  Some
      // download managers prefer the more specific application type;
      // leave the simpler text type since the content is plain text
      // and PowerShell's ``Invoke-Expression`` does not care about
      // Content-Type.
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=300",
    },
  });
};
