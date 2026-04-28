/**
 * Link-validity test for the marketing site.
 *
 * Scans every `.astro` source file in `site/src/` and every `.md` source file
 * under the repo's `docs/` tree, extracts every link target (`href="…"` and
 * `href: "…"` for object-literal data), and validates internal targets resolve.
 *
 * What's checked:
 *
 * 1. **Astro routes** (`/`, `/get/`, `/compare/`, `/404`) must have a matching
 *    file in `site/src/pages/` so the build emits the corresponding HTML.
 * 2. **Docs routes** (`/docs/<slug>/`, `/docs/<sub>/<slug>/`) must have a
 *    matching `.md` file under `docs/` (the MkDocs source tree). Both
 *    `<slug>.md` and `<slug>/index.md` are accepted because MkDocs `use_directory_urls`
 *    treats them equivalently.
 * 3. **Anchors** (`/docs/foo/#bar`) must match a heading in the target `.md`
 *    file, slugified the same way Material for MkDocs slugifies headings.
 * 4. **Static assets** (`/favicon.svg`, `/fonts/…`, `/get/install.sh`) must
 *    exist under `site/public/`.
 * 5. **External URLs** (`https?://…`, `mailto:`, `tel:`) are skipped: no
 *    network calls so the test stays deterministic in CI.
 * 6. **Relative anchors** (`#contact`, `#how-it-works`) are skipped at this
 *    layer; the page builder validates same-page anchor existence at build
 *    time.
 *
 * If a new dynamic route is added (Astro `[param].astro`) or a docs subtree
 * grows new conventions, extend the resolvers below rather than adding
 * special-case skips.
 */

import { describe, expect, it } from "vitest";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";

const SITE_ROOT = resolve(__dirname, "..", "..");
const REPO_ROOT = resolve(SITE_ROOT, "..");
const SITE_SRC = resolve(SITE_ROOT, "src");
const SITE_PUBLIC = resolve(SITE_ROOT, "public");
const SITE_PAGES = resolve(SITE_SRC, "pages");
const DOCS_ROOT = resolve(REPO_ROOT, "docs");

interface LinkOccurrence {
  href: string;
  file: string;
  line: number;
}

/** Walk a directory tree, returning absolute paths of files matching ``predicate``. */
function walk(root: string, predicate: (path: string) => boolean): string[] {
  const out: string[] = [];
  const entries = readdirSync(root);
  for (const entry of entries) {
    if (entry === "node_modules" || entry === "dist" || entry === ".astro") {
      continue;
    }
    const full = join(root, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      out.push(...walk(full, predicate));
    } else if (predicate(full)) {
      out.push(full);
    }
  }
  return out;
}

/**
 * Extract every href value from a source file.
 *
 * Captures both:
 *   - HTML/JSX:   ``href="/foo"``  /  ``href={`/foo/${id}/`}``
 *   - Object data: ``href: "/foo"`` (used by Astro components passing arrays of links)
 *
 * Template-literal hrefs with ``${...}`` interpolations are skipped (we
 * cannot validate them statically); the resolver below records them as
 * ``__dynamic__`` so the test reports their existence in the summary.
 */
function extractHrefs(file: string): LinkOccurrence[] {
  const source = readFileSync(file, "utf-8");
  const occurrences: LinkOccurrence[] = [];
  const lines = source.split("\n");
  // Patterns:
  //   ``href="..."``          (JSX/HTML attribute)
  //   ``href: "..."``         (object literal)
  //   ``href: '...'``         (object literal, single quotes)
  const pattern = /href\s*[=:]\s*"([^"$\n]+)"|href\s*[=:]\s*'([^'$\n]+)'/g;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    let match: RegExpExecArray | null;
    pattern.lastIndex = 0;
    while ((match = pattern.exec(line)) !== null) {
      const href = match[1] ?? match[2];
      if (href === undefined || href === "") {
        continue;
      }
      occurrences.push({ href, file, line: i + 1 });
    }
  }
  return occurrences;
}

/** Slugify the way Material for MkDocs / pymdownx slugify (lowercase, dashes). */
function slugifyHeading(heading: string): string {
  return heading
    .toLowerCase()
    .replace(/`/g, "")
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

/** Collect headings from a markdown file as slug strings. */
function headingSlugsFor(mdFile: string): Set<string> {
  const slugs = new Set<string>();
  const lines = readFileSync(mdFile, "utf-8").split("\n");
  for (const line of lines) {
    const m = /^(#{1,6})\s+(.+?)\s*$/.exec(line);
    if (m) {
      slugs.add(slugifyHeading(m[2]));
    }
  }
  return slugs;
}

/**
 * Resolve a docs path like ``/docs/design/operations/`` to a markdown file.
 *
 * MkDocs ``use_directory_urls: true`` (the default) maps:
 *   ``/docs/foo/``        → ``docs/foo.md`` OR ``docs/foo/index.md``
 *   ``/docs/foo/bar/``    → ``docs/foo/bar.md`` OR ``docs/foo/bar/index.md``
 *   ``/docs/``            → ``docs/index.md``
 */
function resolveDocsPath(pathname: string): string | null {
  // Strip leading "/docs" and surrounding slashes.
  const stripped = pathname.replace(/^\/docs\/?/, "").replace(/\/$/, "");
  if (stripped === "") {
    const candidate = join(DOCS_ROOT, "index.md");
    return existsSync(candidate) ? candidate : null;
  }
  const direct = join(DOCS_ROOT, stripped + ".md");
  if (existsSync(direct)) return direct;
  const indexInside = join(DOCS_ROOT, stripped, "index.md");
  if (existsSync(indexInside)) return indexInside;
  return null;
}

/** Resolve an Astro route to a source page. */
function resolveAstroRoute(pathname: string): string | null {
  // ``/`` -> ``site/src/pages/index.astro``
  // ``/foo/`` -> ``site/src/pages/foo.astro`` OR ``site/src/pages/foo/index.astro``
  // ``/foo`` (no trailing slash) -> same as above (Astro normalises both)
  // ``/foo.bar`` -> ``site/src/pages/foo.bar.astro`` OR ``foo.bar.ts`` /
  //                ``foo.bar.js`` (static endpoints emitted by Astro at the
  //                exact same URL the file path implies)
  const normalised = pathname.replace(/\/$/, "");
  if (normalised === "" || pathname === "/") {
    const candidate = join(SITE_PAGES, "index.astro");
    return existsSync(candidate) ? candidate : null;
  }
  const stripped = normalised.replace(/^\//, "");
  for (const ext of [".astro", ".ts", ".js", ".mts", ".mjs"]) {
    const direct = join(SITE_PAGES, stripped + ext);
    if (existsSync(direct)) return direct;
  }
  const indexInside = join(SITE_PAGES, stripped, "index.astro");
  if (existsSync(indexInside)) return indexInside;
  return null;
}

/** Resolve a static asset under ``site/public/``. */
function resolvePublicAsset(pathname: string): string | null {
  const stripped = pathname.replace(/^\//, "");
  const candidate = join(SITE_PUBLIC, stripped);
  return existsSync(candidate) ? candidate : null;
}

interface ResolutionResult {
  ok: boolean;
  reason?: string;
  /** When the link targets a heading anchor, the markdown file we checked. */
  resolvedFile?: string;
}

function resolveHref(href: string): ResolutionResult {
  // External, mail, tel, javascript:, data: -> skip.
  if (
    /^https?:\/\//.test(href) ||
    href.startsWith("mailto:") ||
    href.startsWith("tel:") ||
    href.startsWith("javascript:") ||
    href.startsWith("data:")
  ) {
    return { ok: true, reason: "external (skipped)" };
  }
  // Same-page anchor: we cannot validate without parsing the rendered
  // page, so skip.
  if (href.startsWith("#")) {
    return { ok: true, reason: "same-page anchor (skipped)" };
  }
  // Sitemap and similar generated artefacts: skip.
  if (href === "/sitemap-index.xml") {
    return { ok: true, reason: "generated by @astrojs/sitemap" };
  }

  // Split path and anchor.
  const [pathOnly, anchor] = href.split("#", 2) as [string, string | undefined];

  // Docs route?
  if (pathOnly.startsWith("/docs/") || pathOnly === "/docs") {
    const resolved = resolveDocsPath(pathOnly);
    if (!resolved) {
      return { ok: false, reason: `no markdown source for ${pathOnly}` };
    }
    if (anchor !== undefined && anchor !== "") {
      const slugs = headingSlugsFor(resolved);
      if (!slugs.has(anchor)) {
        return {
          ok: false,
          reason: `anchor #${anchor} not found in ${relative(REPO_ROOT, resolved)}`,
          resolvedFile: resolved,
        };
      }
    }
    return { ok: true, resolvedFile: resolved };
  }

  // Public asset under /fonts, /favicon.svg, /robots.txt, /get/install.sh, etc.
  if (resolvePublicAsset(pathOnly) !== null) {
    return { ok: true, reason: "public asset" };
  }

  // Astro route in site/src/pages.
  const astro = resolveAstroRoute(pathOnly);
  if (astro !== null) {
    return { ok: true, resolvedFile: astro };
  }

  return { ok: false, reason: `no Astro page or public asset for ${pathOnly}` };
}

const ASTRO_FILES = walk(SITE_SRC, (p) => p.endsWith(".astro"));
const MARKDOWN_FILES = walk(DOCS_ROOT, (p) => p.endsWith(".md"));

const ALL_LINKS: LinkOccurrence[] = [
  ...ASTRO_FILES.flatMap(extractHrefs),
  ...MARKDOWN_FILES.flatMap(extractHrefs),
];

describe("link-validity", () => {
  it("collects a non-trivial number of internal links", () => {
    // Sanity check: if extraction silently breaks, the rest of this file
    // would pass with an empty list.  Pin a floor so a regression in the
    // extractor surfaces immediately.
    const internal = ALL_LINKS.filter(
      (l) => !/^https?:\/\//.test(l.href) && !l.href.startsWith("#"),
    );
    expect(internal.length).toBeGreaterThan(20);
  });

  it("every internal link resolves to a real source artefact", () => {
    const failures: string[] = [];
    const seen = new Map<string, ResolutionResult>();
    for (const link of ALL_LINKS) {
      const cached = seen.get(link.href);
      const result = cached ?? resolveHref(link.href);
      if (!cached) seen.set(link.href, result);
      if (!result.ok) {
        const rel = relative(REPO_ROOT, link.file).replace(/\\/g, "/");
        failures.push(`${rel}:${link.line}  href=${link.href}  ${result.reason}`);
      }
    }
    if (failures.length > 0) {
      // Group identical failures so the same broken href across many
      // sites does not flood the message body.
      const grouped = new Map<string, number>();
      for (const f of failures) grouped.set(f, (grouped.get(f) ?? 0) + 1);
      const summary = [...grouped.entries()]
        .map(([msg, count]) => (count > 1 ? `${msg}  (×${count})` : msg))
        .join("\n");
      throw new Error(
        `Found ${failures.length} broken link occurrence(s) (${grouped.size} unique):\n${summary}`,
      );
    }
  });

  it("docs anchor targets resolve in their markdown source", () => {
    const failures: string[] = [];
    for (const link of ALL_LINKS) {
      if (!link.href.includes("#")) continue;
      if (link.href.startsWith("#")) continue;
      if (/^https?:\/\//.test(link.href)) continue;
      const result = resolveHref(link.href);
      if (!result.ok && result.reason?.startsWith("anchor ")) {
        const rel = relative(REPO_ROOT, link.file).replace(/\\/g, "/");
        failures.push(`${rel}:${link.line}  ${link.href}  ${result.reason}`);
      }
    }
    if (failures.length > 0) {
      throw new Error(
        `Found ${failures.length} broken anchor target(s):\n${failures.join("\n")}`,
      );
    }
  });
});
