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
//   * The synchronous ``document.cookie`` shim used by
//     ``csrf.bench.ts``. Without it the bench would route through
//     jsdom's tough-cookie Promise-based getter, distorting the
//     measured cost (we are timing the parser, not jsdom's cookie
//     jar). Behaviour matches ``test-setup.tsx``'s shim exactly so
//     the bench measures the same code path the production
//     ``getCsrfToken`` reader uses.

const cookieJar: Record<string, string> = Object.create(null) as Record<
  string,
  string
>

if (typeof document !== 'undefined') {
  Object.defineProperty(Document.prototype, 'cookie', {
    configurable: true,
    get: () =>
      Object.entries(cookieJar)
        .map(([k, v]) => `${k}=${v}`)
        .join('; '),
    set: (raw: string) => {
      if (typeof raw !== 'string') return
      const segments = raw.split(';')
      const pair = segments[0] ?? ''
      const eq = pair.indexOf('=')
      if (eq === -1) return
      const name = pair.slice(0, eq).trim()
      const value = pair.slice(eq + 1).trim()
      if (
        !name ||
        name === '__proto__' ||
        name === 'constructor' ||
        name === 'prototype'
      )
        return
      const isDelete = segments.slice(1).some((segment) => {
        const attr = segment.trim().toLowerCase()
        if (attr === 'max-age=0') return true
        if (!attr.startsWith('expires=')) return false
        const expiresAt = Date.parse(attr.slice('expires='.length))
        return Number.isFinite(expiresAt) && expiresAt <= Date.now()
      })
      if (isDelete) {
        delete cookieJar[name]
        return
      }
      cookieJar[name] = value
    },
  })
  // Pre-seed a CSRF cookie so csrf.bench.ts measures the
  // present-token path (the realistic case in production).
  document.cookie = 'csrf_token=bench-csrf-token; path=/'
}
