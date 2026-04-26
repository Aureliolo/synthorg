/**
 * size-limit budgets for the React 19 dashboard bundle.
 *
 * Captured against ``main`` on 2026-04-26 with the existing Vite +
 * Rollup config. Each budget carries ~10% headroom over the captured
 * gzipped size to absorb routine churn without flapping CI; raise a
 * budget intentionally only when a feature legitimately requires more
 * shipping JS, never just to silence a CI red.
 *
 * The chunks are named by Rollup's content-hash output
 * (``vendor-charts-<hash>.js``), so glob patterns ride the hash. We
 * gate the heaviest vendor chunks individually because a ``recharts``
 * minor or ``@xyflow/react`` patch is the most common bundle-bloat
 * vector.
 *
 * Run locally: ``npm --prefix web run build && npm --prefix web run size``
 */
module.exports = [
  // Total JS shipped to a fresh visitor (every chunk concatenated).
  // Catches "renovate accidentally pulls in lodash" style regressions.
  {
    name: 'Total app JS (gzipped)',
    path: 'dist/assets/*.js',
    limit: '950 KB',
    gzip: true,
  },
  // Initial entry chunk -- everything that blocks first paint.
  {
    name: 'Initial entry (gzipped)',
    path: 'dist/assets/index-*.js',
    limit: '10 KB',
    gzip: true,
  },
  // Heaviest vendor chunks -- each typed individually so a regression
  // points right at the offending dependency.
  {
    name: 'vendor-charts (recharts) gzipped',
    path: 'dist/assets/vendor-charts-*.js',
    limit: '125 KB',
    gzip: true,
  },
  {
    name: 'vendor-editor (codemirror) gzipped',
    path: 'dist/assets/vendor-editor-*.js',
    limit: '125 KB',
    gzip: true,
  },
  {
    name: 'vendor-react gzipped',
    path: 'dist/assets/vendor-react-*.js',
    limit: '100 KB',
    gzip: true,
  },
  {
    name: 'vendor-ui gzipped',
    path: 'dist/assets/vendor-ui-*.js',
    limit: '90 KB',
    gzip: true,
  },
  {
    name: 'vendor-flow (xyflow) gzipped',
    path: 'dist/assets/vendor-flow-*.js',
    limit: '80 KB',
    gzip: true,
  },
  {
    name: 'vendor-motion gzipped',
    path: 'dist/assets/vendor-motion-*.js',
    limit: '40 KB',
    gzip: true,
  },
  // Total CSS shipped.
  {
    name: 'Total CSS (gzipped)',
    path: 'dist/assets/*.css',
    limit: '50 KB',
    gzip: true,
  },
]
