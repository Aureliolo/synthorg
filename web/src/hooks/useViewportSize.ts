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
 * The snapshot returns ``{ width: 0, height: 0 }`` during SSR / before
 * subscription, which is a safe pre-paint default for the clamp callers
 * (they're recomputed on the very next render once the resize listener
 * fires the initial value via React's subscription bookkeeping).
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
