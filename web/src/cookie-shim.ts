/**
 * Synchronous ``document.cookie`` shim shared by ``test-setup.tsx``
 * and ``bench-setup.ts``.
 *
 * Why this module exists in isolation:
 *   * jsdom's default ``document.cookie`` is backed by tough-cookie's
 *     Promise-based ``CookieJar``; every read / write schedules a
 *     ``createPromiseCallback``. The shim replaces the prototype
 *     descriptor with a synchronous in-memory jar so the CSRF
 *     benchmark measures actual parser work instead of jsdom's
 *     async cookie machinery (and so test runs stay fast under the
 *     active-handle gate, which would otherwise be bombarded by
 *     downstream allocations triggered from each cookie read).
 *   * Hardening side-benefit: writes routed through the
 *     ``document.cookie`` setter whose parsed key lands on a
 *     prototype slot (``__proto__``, ``constructor``, ``prototype``)
 *     are rejected, so cookie parsing can never pollute the jar's
 *     prototype. Direct assignment on the exported ``cookieJar`` is
 *     out of scope; tests own that surface and the jar is built with
 *     ``Object.create(null)`` so a direct write of ``__proto__``
 *     stores a literal entry instead of mutating the prototype chain.
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

const RESERVED_COOKIE_NAMES = new Set(['__proto__', 'constructor', 'prototype'])

function _isReservedCookieName(name: string): boolean {
  return name === '' || RESERVED_COOKIE_NAMES.has(name)
}

/**
 * Inspect one attribute segment of a `Set-Cookie` string. Returns true
 * when the segment encodes a deletion (Max-Age=0 or a past Expires).
 */
function _segmentMarksDelete(segment: string): boolean {
  const attr = segment.trim().toLowerCase()
  if (attr === 'max-age=0') return true
  if (!attr.startsWith('expires=')) return false
  const expiresAt = Date.parse(attr.slice('expires='.length))
  return Number.isFinite(expiresAt) && expiresAt <= Date.now()
}

function _attrsRequestDelete(segments: readonly string[]): boolean {
  return segments.some(_segmentMarksDelete)
}

function _commitCookieWrite(raw: string): void {
  if (typeof raw !== 'string') return
  const segments = raw.split(';')
  const pair = segments[0] ?? ''
  const eq = pair.indexOf('=')
  if (eq === -1) return
  const name = pair.slice(0, eq).trim()
  if (_isReservedCookieName(name)) return
  const value = pair.slice(eq + 1).trim()
  if (_attrsRequestDelete(segments.slice(1))) {
    Reflect.deleteProperty(cookieJar, name)
    return
  }
  cookieJar[name] = value
}

function _readCookieHeader(): string {
  return Object.entries(cookieJar)
    .map(([k, v]) => `${k}=${v}`)
    .join('; ')
}

export function installCookieShim(seedCsrf?: string): void {
  if (typeof document === 'undefined') return

  Object.defineProperty(Document.prototype, 'cookie', {
    configurable: true,
    get: _readCookieHeader,
    set: _commitCookieWrite,
  })

  if (seedCsrf !== undefined) {
    document.cookie = `csrf_token=${seedCsrf}; path=/`
  }
}
