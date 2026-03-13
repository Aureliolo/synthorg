import { ref, onUnmounted } from 'vue'

/**
 * Poll a function at a fixed interval with cleanup on unmount.
 * Uses setTimeout-based scheduling to prevent overlapping async calls.
 */
export function usePolling(fn: () => Promise<void>, intervalMs: number) {
  const active = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null

  const scheduleTick = () => {
    if (!active.value) return
    timer = setTimeout(async () => {
      if (!active.value) return
      try {
        await fn()
      } catch (err) {
        console.error('Polling error:', err)
      }
      scheduleTick()
    }, intervalMs)
  }

  function start() {
    if (active.value) return
    active.value = true
    // Fetch immediately on start, then schedule subsequent ticks
    const immediate = async () => {
      if (!active.value) return
      try {
        await fn()
      } catch (err) {
        console.error('Polling error:', err)
      }
      scheduleTick()
    }
    immediate()
  }

  function stop() {
    active.value = false
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  onUnmounted(stop)

  return { active, start, stop }
}
