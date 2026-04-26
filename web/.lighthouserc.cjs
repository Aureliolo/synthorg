/**
 * Lighthouse-CI config for the React dashboard.
 *
 * Runs against ``vite preview`` (the production-build static server)
 * so the audit reflects the bundle that ships, not the dev-mode HMR
 * variant. Three URLs cover the common entry points:
 *  - ``/``           : initial route (auth guard usually redirects)
 *  - ``/login``      : the actual first-paint route a fresh visitor lands on
 *  - ``/agents``     : a representative authenticated route
 *
 * The dashboard requires auth + a backend for most routes; ``/login`` and ``/``
 * are the only routes that render meaningfully without state, so the
 * authenticated path is intentionally skipped here -- those are
 * already covered by Storybook's perf characteristics + CodSpeed
 * micro-benchmarks.
 *
 * Run locally:
 *   npm --prefix web run build && npm --prefix web run lighthouse
 */
module.exports = {
  ci: {
    collect: {
      // ``vite preview`` defaults to port 4173; lhci ``startServerCommand``
      // boots the server, runs the audit, then tears it down.
      startServerCommand: 'npm run preview -- --port 4173 --strictPort',
      startServerReadyPattern: 'Local:',
      startServerReadyTimeout: 30000,
      url: [
        'http://localhost:4173/',
        'http://localhost:4173/login',
      ],
      numberOfRuns: 3,
      settings: {
        // Run a desktop-class viewport. Mobile audits introduce noise
        // (network throttling) that we don't gate on -- the dashboard
        // is desktop-first per CLAUDE.md.
        preset: 'desktop',
        chromeFlags: '--no-sandbox --disable-dev-shm-usage',
      },
    },
    assert: {
      // Stricter than the marketing-site config because the dashboard
      // is the operator's primary surface and its perf budget directly
      // affects daily UX.
      assertions: {
        'categories:performance': ['error', { minScore: 0.85 }],
        'categories:accessibility': ['error', { minScore: 0.9 }],
        'categories:best-practices': ['warn', { minScore: 0.9 }],
        // Core Web Vitals
        'largest-contentful-paint': ['error', { maxNumericValue: 2500 }],
        'cumulative-layout-shift': ['error', { maxNumericValue: 0.1 }],
        'total-blocking-time': ['error', { maxNumericValue: 300 }],
      },
    },
    upload: {
      // Public temporary storage; lhci returns a URL that the
      // workflow's PR-comment step links to.
      target: 'temporary-public-storage',
    },
  },
}
