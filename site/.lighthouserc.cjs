/**
 * Lighthouse-CI config for the marketing site.
 *
 * Targets the deployed pr-preview URL (Cloudflare Pages) so the audit
 * reflects the production CDN, not the local dev server. The
 * workflow injects ``LHCI_PR_PREVIEW_URL`` based on the
 * ``Deploy Preview`` job output.
 *
 * Three routes cover the marketing-site surface:
 *  - ``/``        : landing page (most-visited)
 *  - ``/docs/``   : docs index (heaviest, mkdocs + Astro merge)
 *  - ``/get/``    : install page (above-the-fold install snippet)
 */
// Strip any trailing ``/`` so ``${previewBase}/docs/`` does not produce
// a double-slash URL (Cloudflare Pages 308-redirects ``//docs`` to
// ``/docs``, which adds noise to the LHCI run and can flip TBT/LCP).
const previewBase = (
  process.env.LHCI_PR_PREVIEW_URL ?? 'http://localhost:4321'
).replace(/\/+$/, '')

module.exports = {
  ci: {
    collect: {
      url: [
        `${previewBase}/`,
        `${previewBase}/docs/`,
        `${previewBase}/get/`,
      ],
      numberOfRuns: 3,
      settings: {
        preset: 'desktop',
        chromeFlags: '--no-sandbox --disable-dev-shm-usage',
      },
    },
    assert: {
      assertions: {
        // Marketing site has stricter perf expectations -- it's
        // server-rendered Astro static HTML, no JS framework on the
        // hot path. Thresholds below are calibrated to the current
        // measured baseline on the deployed pr-preview URL; tighten
        // them in a follow-up PR alongside the actual a11y / SEO
        // remediation work, do not silently ratchet them up just to
        // make the gate green.
        'categories:performance': ['error', { minScore: 0.9 }],
        // Current baseline ~0.85-0.86 on /docs/. Tightening this
        // requires fixing color-contrast / aria-label issues in the
        // mkdocs-rendered docs theme, tracked separately.
        'categories:accessibility': ['error', { minScore: 0.85 }],
        'categories:best-practices': ['warn', { minScore: 0.9 }],
        // Current baseline ~0.66 on all three URLs (Astro static
        // pages need meta description + canonical link tags added).
        // Kept as 'warn' so it surfaces in the report without
        // blocking the gate; convert to 'error' once SEO work lands.
        'categories:seo': ['warn', { minScore: 0.7 }],
        'largest-contentful-paint': ['error', { maxNumericValue: 2000 }],
        'cumulative-layout-shift': ['error', { maxNumericValue: 0.1 }],
        'total-blocking-time': ['error', { maxNumericValue: 200 }],
      },
    },
    upload: {
      target: 'temporary-public-storage',
    },
  },
}
