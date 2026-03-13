import { ref, onUnmounted } from 'vue'
import { sanitizeForLog } from '@/utils/logging'

const MIN_POLL_INTERVAL = 100

/**
 * Poll a function at a fixed interval with cleanup on unmount.
 * Uses setTimeout-based scheduling to prevent overlapping async calls.
 */
export function usePolling(fn: () => Promise<void>, intervalMs: number) {
  if (!Number.isFinite(intervalMs) || intervalMs < MIN_POLL_INTERVAL) {
    throw new Error(`usePolling: intervalMs must be a finite number >= ${MIN_POLL_INTERVAL}, got ${intervalMs}`)
  }
  const active = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null

  const scheduleTick = () => {
    if (!active.value) return
    timer = setTimeout(async () => {
      if (!active.value) return
      try {
        await fn()
      } catch (err) {
        console.error('Polling error:', sanitizeForLog(err))
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
        console.error('Polling error:', sanitizeForLog(err))
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
