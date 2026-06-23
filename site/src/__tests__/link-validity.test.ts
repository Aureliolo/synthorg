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
  const isMarkdown = file.endsWith(".md");
  // Patterns covered:
  //   1. ``href="..."``       (HTML / JSX attribute, double quote)
  //   2. ``href='...'``       (HTML / JSX attribute, single quote)
  //   3. ``href={"..."}``     (Astro / JSX brace-wrapped string literal)
  //   4. ``href: "..."``      (object-literal data, double quote)
  //   5. ``href: '...'``      (object-literal data, single quote)
  //   6. Markdown ``[text](url)`` links (only scanned in ``.md`` files)
  //
  // Template-literal hrefs with ``${...}`` interpolations are still
  // skipped: we cannot validate them statically.  The ``[^"$\n]`` /
  // ``[^'$\n]`` character class blocks template-literal capture
  // because a captured ``${`` would yield a useless dynamic href.
  const attrPattern =
    /href\s*=\s*"([^"$\n]+)"|href\s*=\s*'([^'$\n]+)'|href\s*=\s*\{\s*"([^"$\n]+)"\s*\}|href\s*=\s*\{\s*'([^'$\n]+)'\s*\}/g;
  const objPattern = /href\s*:\s*"([^"$\n]+)"|href\s*:\s*'([^'$\n]+)'/g;
  // Markdown link pattern: ``[link text](url "optional title")``.  Scope
  // restricted to .md files because curly-quoted prose like ``[note](this)``
  // in TS strings would otherwise match.
  const mdPattern = /\[(?:[^\]]+)\]\(([^\s)]+)(?:\s+"[^"]*")?\)/g;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    for (const pattern of [attrPattern, objPattern]) {
      pattern.lastIndex = 0;
      let match: RegExpExecArray | null;
      while ((match = pattern.exec(line)) !== null) {
        const href = match[1] ?? match[2] ?? match[3] ?? match[4];
        if (href === undefined || href === "") continue;
        occurrences.push({ href, file, line: i + 1 });
      }
    }
    if (isMarkdown) {
      // Strip inline code spans before scanning for markdown links: a code
      // span like ``mock_of[T](**overrides)`` or ``VALIDATORS[t](resolution)``
      // is Python expression syntax, not a ``[text](url)`` link, and must not
      // be mistaken for one.
      const codeless = line.replace(/`[^`]*`/g, "");
      mdPattern.lastIndex = 0;
      let match: RegExpExecArray | null;
      while ((match = mdPattern.exec(codeless)) !== null) {
        const href = match[1];
        if (href === undefined || href === "") continue;
        occurrences.push({ href, file, line: i + 1 });
      }
    }
  }
  return occurrences;
}

/**
 * Slugify the way python-markdown's default ``toc`` extension does (the
 * slugifier Material for MkDocs / Zensical use unless overridden).
 *
 * Faithful to ``markdown.extensions.toc.slugify``:
 *   1. An explicit attr-list id wins: ``## Auth {#auth}`` renders ``id="auth"``,
 *      so the slug is taken verbatim from ``{#...}`` when present.
 *   2. Otherwise: strip inline-code backticks, drop ``[^\w\s-]`` chars in
 *      place (``\w`` keeps ASCII word chars INCLUDING underscore, so
 *      ``RELEASE_BOT_APP_*`` -> ``release_bot_app_``), lowercase, then
 *      collapse every run of dashes/whitespace into a single dash
 *      (``re.sub(r'[-\s]+', '-')``).  A heading like ``AgentEngine <-> TaskEngine
 *      Incremental Sync`` therefore slugifies to the single-dash
 *      ``agentengine-taskengine-incremental-sync``.
 */
function slugifyHeading(heading: string): string {
  const explicit = /\{#([A-Za-z0-9_-]+)[^}]*\}\s*$/.exec(heading);
  if (explicit) return explicit[1].toLowerCase();
  return heading
    .replace(/`/g, "")
    .replace(/[^\w\s-]/g, "")
    .trim()
    .toLowerCase()
    .replace(/[-\s]+/g, "-");
}

/**
 * Collect headings from a markdown file as slug strings.
 *
 * Memoised because ``resolveHref`` calls this once per anchor link, and
 * many cross-references share the same target file.  Without the cache,
 * a doc cluster that links into ``security.md`` 50 times re-reads and
 * re-parses the file 50 times.
 */
const headingCache = new Map<string, Set<string>>();
function headingSlugsFor(mdFile: string): Set<string> {
  const cached = headingCache.get(mdFile);
  if (cached !== undefined) return cached;
  const slugs = new Set<string>();
  const lines = readFileSync(mdFile, "utf-8").split("\n");
  for (const line of lines) {
    const m = /^(#{1,6})\s+(.+?)\s*$/.exec(line);
    if (m) {
      slugs.add(slugifyHeading(m[2]));
    }
    // Explicit anchors usable as link targets but not derived from a
    // heading: HTML ``<a id="x">`` / ``<a name="x">`` (MkDocs renders these
    // verbatim) and attr-list ``{#x}`` ids attached to any block, not just
    // headings.  Without these the resolver false-negatives on valid links.
    const anchorPattern = /(?:\bid|\bname)\s*=\s*["']([A-Za-z0-9_-]+)["']|\{#([A-Za-z0-9_-]+)[^}]*\}/g;
    let a: RegExpExecArray | null;
    while ((a = anchorPattern.exec(line)) !== null) {
      slugs.add((a[1] ?? a[2]).toLowerCase());
    }
  }
  headingCache.set(mdFile, slugs);
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
  const supportedExtensions = [".astro", ".ts", ".js", ".mts", ".mjs"];
  for (const ext of supportedExtensions) {
    const direct = join(SITE_PAGES, stripped + ext);
    if (existsSync(direct)) return direct;
  }
  for (const ext of supportedExtensions) {
    const indexInside = join(SITE_PAGES, stripped, `index${ext}`);
    if (existsSync(indexInside)) return indexInside;
  }
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

// Schemes that are not internal site routes and are intentionally skipped
// by the resolver.  Any URL whose scheme matches one of these short-circuits
// validation: external HTTP(S) URLs are out of scope (we do not network
// hit), and the script / data / mail / tel schemes are not validatable
// at the static link level.  ``vbscript:`` is included for completeness
// (CodeQL ``py/incomplete-url-scheme-check`` flags any check that omits
// it from a non-allowlist scheme test).
const NON_INTERNAL_SCHEMES = [
  "mailto:",
  "tel:",
  "javascript:",
  "vbscript:",
  "data:",
  "file:",
  "blob:",
];

function resolveHref(href: string, sourceFile: string): ResolutionResult {
  if (
    /^https?:\/\//.test(href) ||
    NON_INTERNAL_SCHEMES.some((scheme) => href.startsWith(scheme))
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

  // Relative link from a Markdown file: MkDocs resolves these against
  // the source file's directory.  ``foo.md`` from ``docs/user_guide.md``
  // -> ``docs/foo.md``.  ``../guides/index.md`` from
  // ``docs/design/agents.md`` -> ``docs/guides/index.md``.  Validate
  // both the target file and any anchor.
  const isAbsolute =
    pathOnly.startsWith("/") || /^[a-z][a-z0-9+\-.]*:/i.test(pathOnly);
  if (!isAbsolute && sourceFile.endsWith(".md")) {
    if (pathOnly === "") {
      // Pure anchor in source-relative form (rare; most cases caught by
      // the earlier ``href.startsWith("#")`` branch).
      return { ok: true, reason: "same-page anchor (skipped)" };
    }
    // Relative links to generated/static artefacts (e.g. the API reference
    // ``reference.html`` and ``openapi.json`` emitted into ``docs/openapi/``
    // by the OpenAPI build) are not markdown sources and only exist after
    // that build, so they cannot be validated against the source tree.
    if (pathOnly.endsWith(".html") || pathOnly.endsWith(".json")) {
      return { ok: true, reason: "generated/static artefact (skipped)" };
    }
    const sourceDir = dirname(sourceFile);
    let target = resolve(sourceDir, pathOnly);
    // MkDocs accepts ``../foo.md`` and emits ``../foo/`` at runtime;
    // both forms point at the same source file.  Normalise by
    // stripping trailing slashes and adding ``.md`` if the target is a
    // directory or has no extension.
    if (existsSync(target) && statSync(target).isDirectory()) {
      target = join(target, "index.md");
    } else if (!target.endsWith(".md") && !target.endsWith(".html")) {
      const candidate = target + ".md";
      if (existsSync(candidate)) target = candidate;
    }
    if (!existsSync(target)) {
      return {
        ok: false,
        reason: `no markdown source for relative link ${pathOnly} (resolved to ${relative(REPO_ROOT, target)})`,
      };
    }
    if (anchor !== undefined && anchor !== "" && target.endsWith(".md")) {
      const slugs = headingSlugsFor(target);
      if (!slugs.has(anchor)) {
        return {
          ok: false,
          reason: `anchor #${anchor} not found in ${relative(REPO_ROOT, target)}`,
          resolvedFile: target,
        };
      }
    }
    return { ok: true, resolvedFile: target };
  }

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
    // The cache key includes the source file because relative links
    // resolve against the source's directory, so the same href value
    // can mean different targets from different files.
    const seen = new Map<string, ResolutionResult>();
    for (const link of ALL_LINKS) {
      const cacheKey = `${link.file}::${link.href}`;
      const cached = seen.get(cacheKey);
      const result = cached ?? resolveHref(link.href, link.file);
      if (!cached) seen.set(cacheKey, result);
      if (result.ok) continue;
      // The dedicated anchor-validation test below owns
      // ``anchor #x not found`` failures (it has its own baseline for
      // pre-existing breakage).  Skip them here so the file-resolution
      // test stays focused on missing files / pages / assets.
      if (result.reason?.startsWith("anchor ")) continue;
      const rel = relative(REPO_ROOT, link.file).replace(/\\/g, "/");
      failures.push(`${rel}:${link.line}  href=${link.href}  ${result.reason}`);
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

  // Baseline of pre-existing broken anchors in the docs tree.  Empty: the
  // docs cross-references that previously drifted out of sync with their
  // target headings have been corrected, and the slugifier above now
  // matches real MkDocs/Material output (explicit ``{#id}`` attr-lists,
  // ``\w`` word chars including underscore, and collapsed dash runs), which
  // cleared the remaining entries that were slugifier false-negatives.
  // A NEW genuinely-broken anchor must be fixed at the source link, not
  // re-added here.
  const KNOWN_BROKEN_ANCHORS: ReadonlySet<string> = new Set([]);

  it("docs anchor targets resolve in their markdown source", () => {
    const failures: string[] = [];
    const baselineHits: string[] = [];
    for (const link of ALL_LINKS) {
      if (!link.href.includes("#")) continue;
      if (link.href.startsWith("#")) continue;
      if (/^https?:\/\//.test(link.href)) continue;
      const result = resolveHref(link.href, link.file);
      if (!result.ok && result.reason?.startsWith("anchor ")) {
        const rel = relative(REPO_ROOT, link.file).replace(/\\/g, "/");
        const key = `${rel}:${link.line} :: ${link.href}`;
        if (KNOWN_BROKEN_ANCHORS.has(key)) {
          baselineHits.push(key);
          continue;
        }
        failures.push(`${rel}:${link.line}  ${link.href}  ${result.reason}`);
      }
    }
    if (failures.length > 0) {
      throw new Error(
        `Found ${failures.length} new broken anchor target(s):\n${failures.join("\n")}`,
      );
    }
    // If a baseline entry no longer matches a real broken link, drop it
    // from the set so the baseline cannot grow stale in the other
    // direction.
    const unused = [...KNOWN_BROKEN_ANCHORS].filter(
      (k) => !baselineHits.includes(k),
    );
    if (unused.length > 0) {
      throw new Error(
        `KNOWN_BROKEN_ANCHORS contains ${unused.length} stale entries; remove them:\n${unused.join("\n")}`,
      );
    }
  });
});
