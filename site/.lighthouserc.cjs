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
const previewBase = process.env.LHCI_PR_PREVIEW_URL ?? 'http://localhost:4321'

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
        // hot path.
        'categories:performance': ['error', { minScore: 0.9 }],
        'categories:accessibility': ['error', { minScore: 0.9 }],
        'categories:best-practices': ['warn', { minScore: 0.9 }],
        'categories:seo': ['warn', { minScore: 0.9 }],
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
