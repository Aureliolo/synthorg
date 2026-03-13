import { ref, onUnmounted } from 'vue'

/**
 * Poll a function at a fixed interval with cleanup on unmount.
 * Wraps the async function in error handling to prevent unhandled rejections.
 */
export function usePolling(fn: () => Promise<void>, intervalMs: number) {
  const active = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null

  const safeFn = async () => {
    if (!active.value) return
    try {
      await fn()
    } catch (err) {
      console.error('Polling error:', err)
    }
  }

  function start() {
    if (active.value) return
    active.value = true
    safeFn() // fetch immediately on start, don't wait for first interval
    timer = setInterval(safeFn, intervalMs)
  }

  function stop() {
    active.value = false
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  onUnmounted(stop)

  return { active, start, stop }
}
