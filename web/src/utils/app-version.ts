/**
 * App-version-gated cookie and storage hygiene on boot.
 *
 * When the bundled build id differs from the one previously seen by this
 * browser, we invalidate all client-side auth state before the rest of
 * the app runs. This prevents stale cookies (session, csrf_token,
 * refresh_token) from tripping CSRF 403s or JWT-shape mismatches after
 * a backend upgrade -- the exact class of bug that manifested on the
 * setup wizard when an old session cookie survived a backend reset but
 * the csrf_token cookie did not.
 *
 * The build id is baked into the bundle at build time by vite.config.ts
 * (defaults to package.json#version; CI overrides with SYNTHORG_BUILD_ID).
 */

import { apiClient } from '@/api/client'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('app-version')

const STORAGE_KEY = 'synthorg:app_build_id'

/** Abort deadline for the best-effort server logout call, in ms. */
const LOGOUT_TIMEOUT_MS = 2000

/** Current build id, substituted at build time by Vite `define`. */
const CURRENT_BUILD_ID: string = import.meta.env.VITE_APP_BUILD_ID ?? 'dev'

/**
 * Cookies that JavaScript can observe or clear. The session and refresh
 * cookies are HttpOnly; only the server can clear those (via /auth/logout).
 * csrf_token is intentionally non-HttpOnly per the double-submit pattern.
 */
const CLIENT_VISIBLE_COOKIES = ['csrf_token'] as const

/**
 * Ensure the browser's persisted state matches the current build id. On
 * mismatch, POST /auth/logout (idempotent on the server -- emits clear
 * cookies regardless of session validity), wipe localStorage and
 * sessionStorage, and hard-reload so the app boots against clean state.
 *
 * First-ever load (no stored id) simply stamps the current id and
 * returns -- nothing to clean, and we don't want to force a redundant
 * logout round-trip on every fresh install.
 */
export async function ensureFreshAppState(): Promise<void> {
  let stored: string | null
  try {
    stored = localStorage.getItem(STORAGE_KEY)
  } catch (err) {
    // Private mode / storage disabled -- no version gate possible; let
    // the rest of the app bootstrap normally.
    log.warn('localStorage unavailable; skipping version check', err)
    return
  }

  if (stored === CURRENT_BUILD_ID) return

  if (stored === null) {
    try {
      localStorage.setItem(STORAGE_KEY, CURRENT_BUILD_ID)
    } catch (err) {
      log.warn('Failed to stamp build id on first load', err)
    }
    return
  }

  // ``stored`` comes from localStorage and is attacker-controllable
  // (an XSS payload could overwrite it with arbitrary text).  Route
  // the payload through ``sanitizeForLog`` so it can't inject control
  // characters or log-forging sequences into the output.
  log.warn(
    'App build id changed; clearing stale client state',
    sanitizeForLog({ stored, current: CURRENT_BUILD_ID }),
  )

  await callServerLogout()
  clearClientVisibleCookies()
  clearBrowserStorage()

  try {
    localStorage.setItem(STORAGE_KEY, CURRENT_BUILD_ID)
  } catch (err) {
    // Storage is unavailable or quota-exceeded.  Skip the reload;
    // triggering it now would loop forever (next boot would see the
    // same mismatch, call logout again, fail to stamp again, reload).
    // Fall through to normal app bootstrap with the (now-cleared)
    // state so the user can continue, even if subsequent boots repeat
    // the clear cycle.
    log.error(
      'Failed to persist new build id after clear; skipping reload to avoid an infinite reload loop',
      err,
    )
    return
  }

  // Hard reload so every module re-executes against fresh state.
  // Note: the no-arg ``location.reload()`` respects HTTP caches
  // (JS/CSS may be 304 Not Modified).  The deprecated
  // ``location.reload(true)`` forceReload flag was removed from all
  // major browsers; fresh assets rely on standard cache-busting
  // (hashed filenames / server ``Cache-Control`` headers).
  window.location.reload()
  // Return a never-resolving promise so callers don't continue bootstrap
  // during the reload window (keeps the loading splash visible).
  await new Promise(() => {})
}

async function callServerLogout(): Promise<void> {
  // Bound the best-effort call with an ``AbortController`` -- boot is
  // awaiting this, and an unbounded stall would block the entire app
  // from mounting.  Falling through to the local clear + reload is
  // safe (HttpOnly cookies survive, but the next real API call will
  // 401 and the login flow will overwrite them).
  const controller = new AbortController()
  const timeoutId = window.setTimeout(
    () => controller.abort(),
    LOGOUT_TIMEOUT_MS,
  )
  try {
    // Routed through the shared axios client so the boot path uses one HTTP
    // stack. The 2s abort deadline above makes 429 retry-after handling moot
    // (any honoured Retry-After would outlast the deadline), so a plain POST
    // is sufficient. /auth/logout is CSRF-exempt, so it still clears stale
    // state even when the csrf_token cookie is gone and no header is attached.
    await apiClient.post('/auth/logout', null, { signal: controller.signal })
  } catch (err) {
    log.warn('Logout call failed during stale-state recovery', err)
  } finally {
    window.clearTimeout(timeoutId)
  }
}

function clearClientVisibleCookies(): void {
  // Belt-and-braces in case the logout response never made it back
  // (network failure mid-request). We can't clear HttpOnly cookies
  // from JS, so this only covers csrf_token.
  for (const name of CLIENT_VISIBLE_COOKIES) {
    document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Strict`
  }
}

function clearBrowserStorage(): void {
  try {
    localStorage.clear()
  } catch (err) {
    log.warn('Failed to clear localStorage', err)
  }
  try {
    sessionStorage.clear()
  } catch (err) {
    log.warn('Failed to clear sessionStorage', err)
  }
}
