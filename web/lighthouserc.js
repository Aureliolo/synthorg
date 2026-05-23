/** @type {import('@lhci/utils').Config} */
export default {
  ci: {
    collect: {
      numberOfRuns: 3,
      startServerCommand: 'npm run preview',
      startServerReadyPattern: 'Local:',
      url: ['http://localhost:4173/login', 'http://localhost:4173/'],
      settings: {
        preset: 'desktop',
        chromeFlags: '--headless --no-sandbox',
      },
    },
    assert: {
      // Aggressive performance / a11y / best-practices / SEO budget.
      // perf >= 0.90, a11y >= 0.95, best-practices >= 0.90, seo >= 0.90.
      // Web Vitals: CLS <= 0.05, LCP <= 2.5s, TBT <= 300ms.
      // Hard-blocking from day one.
      assertions: {
        'categories:performance': ['error', { minScore: 0.9 }],
        'categories:accessibility': ['error', { minScore: 0.95 }],
        'categories:best-practices': ['error', { minScore: 0.9 }],
        'categories:seo': ['error', { minScore: 0.9 }],
        'cumulative-layout-shift': ['error', { maxNumericValue: 0.05 }],
        'largest-contentful-paint': ['error', { maxNumericValue: 2500 }],
        'total-blocking-time': ['error', { maxNumericValue: 300 }],
      },
    },
    upload: {
      target: 'temporary-public-storage',
    },
  },
}
