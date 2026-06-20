import type { Page } from '@playwright/test'

/**
 * Seed a mock authenticated session into ``localStorage`` so a spec lands
 * on an authenticated route without driving the login UI.
 *
 * This uses ``addInitScript`` rather than the 1.61 ``page.localStorage``
 * WebStorage API on purpose: the auth store reads these keys during app
 * init (on the very first ``goto``), and ``page.localStorage`` only
 * operates on the *current* origin's live storage -- before navigation
 * the page sits on ``about:blank`` with no origin, so a ``setItem`` there
 * would seed nothing. ``addInitScript`` runs on every document creation,
 * guaranteeing the token is present before the SPA boots.
 *
 * ``freezeTime`` (if a spec calls it first) overrides ``Date.now`` to a
 * fixed instant, so the expiry below is computed against the frozen clock
 * -- still a day ahead, which is all the store's init check requires.
 */
export async function seedAuth(page: Page): Promise<void> {
  const ONE_DAY_MS = 86_400_000
  await page.addInitScript((expiryWindowMs) => {
    localStorage.setItem('auth_token', 'mock-token')
    localStorage.setItem(
      'auth_token_expires_at',
      String(Date.now() + expiryWindowMs),
    )
  }, ONE_DAY_MS)
}
