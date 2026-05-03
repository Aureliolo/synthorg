/**
 * Side-effect-free teardown for the org-chart-prefs persist middleware.
 *
 * Mirrors `setup-wizard/teardown.ts`: imported from
 * `web/src/test-setup.tsx`'s global `afterEach` to drop the persisted
 * key so state does not leak across tests in the same Vitest worker.
 * The key constant lives here (not in `org-chart-prefs.ts`) so the
 * test-setup import does not transitively run the store's
 * `create()` + `persist()` factory; the store imports the constant
 * back from this module.
 */
export const ORG_CHART_PREFS_PERSIST_NAME = 'synthorg:orgchart:prefs'

export function cancelOrgChartPrefsPersist(): void {
  if (typeof globalThis === 'undefined') return
  let storage: Storage | undefined
  try {
    storage = (globalThis as { localStorage?: Storage }).localStorage
  } catch {
    return
  }
  if (storage === undefined) return
  try {
    storage.removeItem(ORG_CHART_PREFS_PERSIST_NAME)
  } catch {
    // Some test envs strip localStorage; safe to ignore.
  }
}
