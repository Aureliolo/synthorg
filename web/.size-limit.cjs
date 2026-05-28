/**
 * size-limit budgets for the React 19 dashboard bundle.
 *
 * Captured gzipped sizes on 2026-04-26 against ``main`` with the
 * existing Vite + Rollup config. Per-vendor budgets carry ~10-15%
 * headroom; the total-app-JS budget carries ~5% headroom (current
 * ~950 KB gzipped, ceiling 1000 KB). Re-baselined 2026-05-15 for
 * the pydantic-to-typescript codegen pipeline (PR #1909): the
 * generated ``enum-values.gen.ts`` ships runtime ``*_VALUES`` tuples
 * for ~90 backend StrEnums that the dashboard relies on for select
 * options and type guards (where many were previously inline literal
 * arrays); the migration trades ~50 KB of code-duplicated literals
 * for ~70 KB of generated tuples, net ~20 KB growth absorbed by the
 * fresh ceiling plus ~5% headroom for ongoing codegen additions.
 * Re-baselined again 2026-05-28 for EPIC #2066 D2a decomposition
 * (PR #2154): bringing 13 page families under the ESLint size caps
 * (``complexity: 8``, ``max-lines: 400``, ``max-lines-per-function:
 * 80``, ``max-params: 5``) requires extracting ~24 controller hooks
 * / helper modules. Each new file adds module-boundary overhead
 * (imports, exports, type declarations, named-function trailers)
 * that doesn't fully gzip away; the cumulative cost is ~10 KB
 * gzipped across the touched pages. The structural caps are the
 * load-bearing constraint (they unblock the per-bucket ``ignores:``
 * deletions tracked by EPIC #2066) -- absorbing the unavoidable
 * boundary overhead is the legitimate way to make that landable.
 * Headroom absorbs routine dependency-update churn without flapping
 * CI -- raise a budget intentionally only when a feature legitimately
 * requires more shipping JS, never just to silence a CI red.
 * To re-baseline:
 *   npm --prefix web run build
 *   npm --prefix web run size
 *   # then update each ``limit:`` to (current-size * 1.10) and document
 *   # the bump in the PR description.
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
    limit: '1100 KB',
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
