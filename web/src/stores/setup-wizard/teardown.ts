/**
 * Side-effect-free teardown for the setup-wizard persist middleware.
 *
 * Imported by ``web/src/test-setup.tsx`` from the global ``afterEach``.
 * Must NOT transitively load ``@/api/client`` (the in-file comment in
 * test-setup.tsx explains why); doing so would capture an unmocked
 * ``getCsrfToken`` reference before per-test ``vi.mock('@/utils/csrf')``
 * can hoist.  We therefore reach localStorage directly with the
 * persist-key constant and never import the composed store here.
 */
import { SETUP_WIZARD_PERSIST_NAME } from './persist-key'

export function cancelSetupWizardPersist(): void {
  if (typeof globalThis === 'undefined') return
  const storage = (globalThis as { localStorage?: Storage }).localStorage
  if (storage === undefined) return
  try {
    storage.removeItem(SETUP_WIZARD_PERSIST_NAME)
  } catch {
    // Some test envs strip localStorage; safe to ignore.
  }
}
