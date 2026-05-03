import { useSyncExternalStore } from 'react'

/**
 * Reactive viewport-size hook backed by ``useSyncExternalStore``.
 *
 * Encapsulates the ``window.innerWidth`` / ``window.innerHeight`` reads so
 * call sites don't touch globals during render (satisfies the
 * ``react-x/globals`` rule) AND values stay in sync with viewport
 * resizes -- direct render-time reads were stale until React re-rendered
 * for some other reason.
 *
 * In a browser context (CSR / Vite) ``readSnapshot()`` is called
 * synchronously during the first render and returns the real
 * ``window.inner*`` dimensions immediately; no resize event is needed
 * to prime the values. ``SSR_SNAPSHOT`` (``{ width: 0, height: 0 }``)
 * is the server snapshot argument to ``useSyncExternalStore`` and is
 * only used during SSR or when ``window`` is undefined.
 */
export interface ViewportSize {
  width: number
  height: number
}

const SSR_SNAPSHOT: ViewportSize = { width: 0, height: 0 }
let cachedSnapshot: ViewportSize = SSR_SNAPSHOT

function readSnapshot(): ViewportSize {
  if (typeof window === 'undefined') return SSR_SNAPSHOT
  // Recompute the cached snapshot only when dimensions actually changed
  // so getSnapshot returns a referentially-stable value between resizes
  // (React's useSyncExternalStore demands snapshot identity stability).
  const width = window.innerWidth
  const height = window.innerHeight
  if (cachedSnapshot.width !== width || cachedSnapshot.height !== height) {
    cachedSnapshot = { width, height }
  }
  return cachedSnapshot
}

function subscribe(onStoreChange: () => void): () => void {
  if (typeof window === 'undefined') return () => undefined
  window.addEventListener('resize', onStoreChange)
  return () => window.removeEventListener('resize', onStoreChange)
}

export function useViewportSize(): ViewportSize {
  return useSyncExternalStore(subscribe, readSnapshot, () => SSR_SNAPSHOT)
}
