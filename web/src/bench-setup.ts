// Bench-mode setup for ``vitest bench`` -- loaded ONLY by the
// ``bench`` project in ``vitest.config.ts``. Test-mode setup
// (``test-setup.tsx``) is loaded by the ``unit`` project; the two
// never share a worker.
//
// Why this file is minimal:
//   * Bench files exercise pure helpers (formatters, sanitisers,
//     paginators). They never make network requests, never render
//     React, never animate, never touch toasts.
//   * MSW's ``setupServer().listen()`` patches Node global HTTP
//     interceptors. Calling it once per ``.bench.ts`` file (which
//     happens because ``test.setupFiles`` was previously shared
//     with bench) trips MSW's invariant on the second listen and
//     fails the whole CodSpeed Web job. With a dedicated bench
//     setup, MSW is never imported in bench mode at all.
//   * Motion / matchMedia / rAF shims are not needed for benches:
//     no Component renders, no media queries are read, no
//     animation frames are scheduled.
//
// What this file DOES install:
//   * The synchronous ``document.cookie`` shim (shared with
//     ``test-setup.tsx`` via ``@/cookie-shim``). Without it the
//     bench would route through jsdom's tough-cookie Promise-based
//     getter, distorting the measured cost (we are timing the
//     parser, not jsdom's cookie jar). The shim module imports
//     nothing else, so bench mode stays MSW-free.

import { installCookieShim } from '@/cookie-shim'

// Pre-seed a CSRF cookie so ``csrf.bench.ts`` measures the
// present-token path (the realistic case in production).
installCookieShim('bench-csrf-token')
