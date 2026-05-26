/** React-safe hook for the sidebar's user-collapsed preference.
 *
 * The underlying storage is ``window.localStorage`` keyed by
 * ``STORAGE_KEY``. All direct DOM-global access is encapsulated inside
 * the hook so component render bodies stay free of ``localStorage``
 * reads (per the `@eslint-react/globals` rule in `web/eslint.config.js`,
 * which forbids window/document/localStorage in render).
 */

import { useCallback, useSyncExternalStore } from 'react'

export const STORAGE_KEY = 'sidebar_collapsed'

function _readSnapshot(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

function _writeSnapshot(value: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(value))
  } catch {
    // Ignore: storage may be unavailable (e.g. quota exceeded).
  }
}

function _serverSnapshot(): boolean {
  // SSR / initial-hydration fallback: ``localStorage`` is undefined on
  // the server, so default to "not collapsed" until the client mounts
  // and ``getSnapshot`` returns the persisted value.
  return false
}

function _subscribe(callback: () => void): () => void {
  // Cross-tab sync: ``storage`` only fires for OTHER documents on the
  // same origin (the spec's "storage event same-document" carve-out is
  // intentional). That is exactly the cross-tab signal we want here;
  // intra-tab updates flow through ``setCollapsed`` and trigger a
  // re-render via the explicit dispatch below.
  const handler = (event: StorageEvent) => {
    if (event.key === null || event.key === STORAGE_KEY) callback()
  }
  window.addEventListener('storage', handler)
  return () => window.removeEventListener('storage', handler)
}

const _localListeners = new Set<() => void>()

function _subscribeLocal(callback: () => void): () => void {
  _localListeners.add(callback)
  return () => {
    _localListeners.delete(callback)
  }
}

function _notifyLocal(): void {
  for (const listener of _localListeners) listener()
}

function _subscribeAll(callback: () => void): () => void {
  const unsubStorage = _subscribe(callback)
  const unsubLocal = _subscribeLocal(callback)
  return () => {
    unsubStorage()
    unsubLocal()
  }
}

/**
 * Subscribe a component to the sidebar collapsed-state preference.
 *
 * Returns ``[collapsed, setCollapsed]``. ``setCollapsed`` writes the
 * new value to ``localStorage`` AND notifies subscribed components in
 * the same tab; cross-tab updates arrive via the ``storage`` event.
 */
export function useCollapsedState(): readonly [boolean, (value: boolean) => void] {
  const collapsed = useSyncExternalStore(_subscribeAll, _readSnapshot, _serverSnapshot)
  const setCollapsed = useCallback((value: boolean) => {
    _writeSnapshot(value)
    _notifyLocal()
  }, [])
  return [collapsed, setCollapsed]
}
