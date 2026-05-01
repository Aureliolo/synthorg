/**
 * Synchronous ``document.cookie`` shim shared by ``test-setup.tsx``
 * and ``bench-setup.ts``.
 *
 * Why this module exists in isolation:
 *   * jsdom's default ``document.cookie`` is backed by tough-cookie's
 *     Promise-based ``CookieJar``. Every read / write schedules a
 *     ``createPromiseCallback`` that vitest's ``--detect-async-leaks``
 *     flags as a leaked Promise. The shim replaces the descriptor on
 *     ``Document.prototype`` with a synchronous in-memory jar so the
 *     leak count stays under the CI ceiling AND so the CSRF
 *     benchmark measures actual parser work instead of jsdom's
 *     async cookie machinery.
 *   * The shim has zero runtime dependencies. Importing it from
 *     ``bench-setup.ts`` does NOT pull in MSW, Motion mocks, the
 *     toast store, or anything else that would defeat the
 *     "benchmark setup must stay minimal" rule.
 *
 * Public surface:
 *   * ``installCookieShim(seedCsrf?)`` -- replaces the prototype
 *     descriptor and (optionally) seeds a ``csrf_token=<value>``
 *     entry. Idempotent: calling twice in the same process replaces
 *     the prior shim cleanly because ``Object.defineProperty`` on an
 *     already-shimmed prototype just rebinds the descriptor.
 *   * ``cookieJar`` -- the underlying record, exported so test-mode
 *     teardown hooks can wipe per-test cookie state without
 *     re-touching the DOM (avoids jsdom's tough-cookie cost).
 *
 * Behaviour parity with the previous in-line copies:
 *   * ``get`` returns ``k=v; k=v`` exactly.
 *   * ``set`` parses the first ``k=v`` pair, ignores the rest unless
 *     the ``Max-Age=0`` / past ``Expires=`` semantics indicate a
 *     delete.
 *   * Prototype-pollution guard: ``__proto__`` / ``constructor`` /
 *     ``prototype`` keys are rejected so future refactors that
 *     iterate the jar into a prototype-carrying object cannot
 *     weaponise them.
 *   * ``Object.create(null)`` jar so ``cookieJar[name] = value``
 *     cannot ever fall through to ``Object.prototype``.
 */

export const cookieJar: Record<string, string> = Object.create(null) as Record<
  string,
  string
>

export function installCookieShim(seedCsrf?: string): void {
  if (typeof document === 'undefined') return

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

  if (seedCsrf !== undefined) {
    document.cookie = `csrf_token=${seedCsrf}; path=/`
  }
}
