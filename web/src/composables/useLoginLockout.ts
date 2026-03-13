/**
 * Shared client-side lockout logic for Login and Setup pages.
 * This is a UX hint only — real brute-force protection is server-side.
 */

import { ref, computed, onUnmounted } from 'vue'
import { isAxiosError } from '@/utils/errors'
import { LOGIN_MAX_ATTEMPTS, LOGIN_LOCKOUT_MS } from '@/utils/constants'

export function useLoginLockout() {
  const attempts = ref(0)
  const lockedUntil = ref<number | null>(null)

  // Reactive clock so `locked` re-evaluates when lockout expires
  const now = ref(Date.now())
  const clockTimer = setInterval(() => { now.value = Date.now() }, 1000)
  onUnmounted(() => clearInterval(clockTimer))

  const locked = computed(() => !!(lockedUntil.value && now.value < lockedUntil.value))

  /** Clear expired lockout. Returns true if still locked. */
  function checkAndClearLockout(): boolean {
    if (lockedUntil.value && Date.now() >= lockedUntil.value) {
      lockedUntil.value = null
      attempts.value = 0
    }
    return locked.value
  }

  /**
   * Record a failed attempt. Uses HTTP status code (not error message strings)
   * to distinguish credential errors (4xx) from transient failures (network/5xx).
   * Returns a lockout error message if the user just got locked out, or null.
   */
  function recordFailure(err: unknown): string | null {
    // Only count credential failures (4xx) toward lockout
    const isCredentialError = isAxiosError(err) &&
      err.response !== undefined &&
      err.response.status >= 400 &&
      err.response.status < 500

    if (isCredentialError) {
      attempts.value++
      if (attempts.value >= LOGIN_MAX_ATTEMPTS) {
        lockedUntil.value = Date.now() + LOGIN_LOCKOUT_MS
        attempts.value = 0
        return `Too many failed attempts. Please wait ${LOGIN_LOCKOUT_MS / 1000} seconds.`
      }
    }
    return null
  }

  /** Reset attempts on successful auth. */
  function reset() {
    attempts.value = 0
    lockedUntil.value = null
  }

  return { locked, checkAndClearLockout, recordFailure, reset }
}
