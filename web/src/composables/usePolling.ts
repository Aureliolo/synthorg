import { ref, onUnmounted } from 'vue'

/**
 * Poll a function at a fixed interval with cleanup on unmount.
 */
export function usePolling(fn: () => Promise<void>, intervalMs: number) {
  const active = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null

  function start() {
    if (active.value) return
    active.value = true
    fn() // initial call
    timer = setInterval(fn, intervalMs)
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
