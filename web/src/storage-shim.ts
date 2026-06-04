/**
 * Synchronous ``Storage.prototype`` patch loaded from
 * ``test-setup.tsx``.
 *
 * Why this module exists:
 *   * jsdom's ``StorageImpl`` schedules a
 *     ``setTimeout(this._dispatchStorageEvent.bind(this), 0, ...)`` on
 *     every ``setItem`` / ``removeItem`` / ``clear`` call so other
 *     same-origin tabs can react to writes. In a single-tab test
 *     environment those timers are pure overhead. The dashboard
 *     reaches localStorage from two paths -- Zustand's ``persist``
 *     middleware (setup-wizard, org-chart-prefs) and direct
 *     ``localStorage.setItem`` calls (theme, notifications) -- so the
 *     timer overhead compounds across every store-mutating test.
 *   * No app or test code subscribes to the ``storage`` event
 *     (verified via ``addEventListener('storage')`` grep), so the
 *     dispatch is dead weight in the test runner.
 *
 * Why prototype patch (not property replacement):
 *   Some tests (``app-version.test.ts``) ``vi.spyOn(Storage.prototype,
 *   'setItem')`` to inject quota-exceeded errors. Replacing
 *   ``window.localStorage`` with a different class would make those
 *   spies miss the call. Patching ``Storage.prototype`` keeps
 *   ``localStorage instanceof Storage === true`` and lets the spies
 *   intercept normally; mockRestore() restores back to the patched
 *   version (which is what we want -- the original would re-introduce
 *   the per-write timer overhead).
 *
 * Storage isolation:
 *   Each ``Storage`` instance (one for ``localStorage``, one for
 *   ``sessionStorage``) gets its own ``Map`` via a module-level
 *   ``WeakMap`` keyed on the instance. Per-test isolation continues
 *   to live in caller hooks (``cancelSetupWizardPersist`` etc.):
 *   this module does NOT auto-reset between tests, matching jsdom's
 *   existing semantics.
 *
 * Prototype-pollution stance: the bucket is a ``Map``, so
 * prototype-slot keys (``__proto__`` etc.) are stored as ordinary map
 * entries and cannot mutate the prototype chain. No explicit key
 * filter is required.
 */

const stores = new WeakMap<Storage, Map<string, string>>()

function bucket(storage: Storage): Map<string, string> {
  let map = stores.get(storage)
  if (map === undefined) {
    map = new Map()
    stores.set(storage, map)
  }
  return map
}

export function installStorageShim(): void {
  if (typeof Storage === 'undefined') return

  Storage.prototype.setItem = function setItem(
    this: Storage,
    key: string,
    value: string,
  ): void {
    bucket(this).set(key, value)
  }

  Storage.prototype.getItem = function getItem(
    this: Storage,
    key: string,
  ): string | null {
    return bucket(this).get(key) ?? null
  }

  Storage.prototype.removeItem = function removeItem(
    this: Storage,
    key: string,
  ): void {
    bucket(this).delete(key)
  }

  Storage.prototype.clear = function clear(this: Storage): void {
    bucket(this).clear()
  }

  Storage.prototype.key = function key(
    this: Storage,
    index: number,
  ): string | null {
    const map = bucket(this)
    // Match the Web Storage spec: ``key(index)`` coerces ``index``
    // to ``unsigned long``, so non-integer numbers truncate toward
    // zero and NaN / negative / out-of-range values return null.
    const i = Math.trunc(index)
    if (!Number.isFinite(i) || i < 0 || i >= map.size) return null
    let cursor = 0
    for (const k of map.keys()) {
      if (cursor === i) return k
      cursor += 1
    }
    return null
  }

  Object.defineProperty(Storage.prototype, 'length', {
    configurable: true,
    get(this: Storage): number {
      return bucket(this).size
    },
  })
}
