# Marketing site security posture

The marketing site builds to static HTML (`astro build`) and ships from two
surfaces with different header capabilities. Read this before assuming a
response header is live in production.

## Two deploy surfaces

- **PR preview** -> Cloudflare Pages. Honours `public/_headers`, so the full
  response-header set below is live on previews.
- **Production (synthorg.io)** -> GitHub Pages (`.github/workflows/build-docs.yml`,
  `actions/deploy-pages`). GitHub Pages does NOT support a `_headers` file and
  cannot send custom response headers.

## What protects production

Production hardening is the Content-Security-Policy emitted as a
`<meta http-equiv>` on every page (configured in `astro.config.mjs` ->
`security.csp`; Astro hashes every bundled and inline script/style, so no
`'unsafe-inline'` is needed) plus markup hygiene. The `<meta>` CSP covers
`default-src`, `script-src`, `style-src`, `img-src`, `font-src`, `connect-src`,
`frame-src`, `form-action`, `base-uri`, `object-src`, and
`upgrade-insecure-requests`.

## RESIDUAL GAP: headers absent in production

The following are response-header-only protections. They are delivered on the
Cloudflare preview surface via `public/_headers` but are ABSENT on production
GitHub Pages, because neither a `<meta>` tag nor any in-repo change can emit
them there:

- `Strict-Transport-Security` (HSTS / preload)
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy`
- `Permissions-Policy`
- `Cross-Origin-Opener-Policy` / `Cross-Origin-Resource-Policy`
- `frame-ancestors 'none'` / `X-Frame-Options: DENY` (clickjacking defence;
  `frame-ancestors` cannot be expressed via `<meta>`)

## Closing the gap

The only ways to deliver these in production are infrastructure changes, not
repo changes:

1. Front synthorg.io through Cloudflare and add response-header (Transform)
   rules at the edge, OR
2. Move production hosting from GitHub Pages to Cloudflare Pages, where
   `public/_headers` is honoured directly.

Until one of those is in place, treat the headers above as preview-only and
the production gap as accepted for a static marketing site with no
authenticated surface.
