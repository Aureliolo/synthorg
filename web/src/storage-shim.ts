/**
 * Synchronous ``Storage.prototype`` patch loaded from
 * ``test-setup.tsx``.
 *
 * Why this module exists:
 *   * jsdom's ``StorageImpl`` schedules a
 *     ``setTimeout(this._dispatchStorageEvent.bind(this), 0, ...)`` on
 *     every ``setItem`` / ``removeItem`` / ``clear`` call so other
 *     same-origin tabs can react to writes. In a single-tab test
 *     environment those timers are pure overhead AND
 *     ``--detect-async-leaks`` flags every undrained one as a Timeout
 *     leak. The dashboard reaches localStorage from two paths --
 *     Zustand's ``persist`` middleware (setup-wizard, org-chart-prefs)
 *     and direct ``localStorage.setItem`` calls (theme, notifications)
 *     -- so any test that mutates one of those stores contributes to
 *     the count.
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
 *   the leak).
 *
 * Storage isolation:
 *   Each ``Storage`` instance (one for ``localStorage``, one for
 *   ``sessionStorage``) gets its own ``Map`` via a module-level
 *   ``WeakMap`` keyed on the instance. Per-test isolation continues
 *   to live in caller hooks (``cancelSetupWizardPersist`` etc.) --
 *   this module does NOT auto-reset between tests, matching jsdom's
 *   existing semantics.
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
    bucket(this).set(String(key), String(value))
  }

  Storage.prototype.getItem = function getItem(
    this: Storage,
    key: string,
  ): string | null {
    return bucket(this).get(String(key)) ?? null
  }

  Storage.prototype.removeItem = function removeItem(
    this: Storage,
    key: string,
  ): void {
    bucket(this).delete(String(key))
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
    const i = Math.trunc(Number(index))
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
