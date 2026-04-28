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
import { resolve } from "node:path";

import type { APIRoute } from "astro";

export const prerender = true;

export const GET: APIRoute = () => {
  const scriptPath = resolve("../cli/scripts/install.ps1");
  const content = readFileSync(scriptPath, "utf-8");
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
