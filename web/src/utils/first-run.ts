/**
 * Shared contract for the "first run after setup" flag.
 *
 * Setup completion (or skip) sets this localStorage flag; the dashboard
 * reads it once to surface the post-setup guidance card, then clears it.
 * Keeping the key and value in one module stops the writers (CompleteStep,
 * SkipWizardForm) and the reader (DashboardPage) drifting apart on a magic
 * string / magic `'1'`.
 */

/** localStorage key carrying the first-run flag. */
const FIRST_RUN_STORAGE_KEY = 'synthorg.firstRun'

/** Sentinel value written when setup finishes; any other value reads false. */
const FIRST_RUN_FLAG_VALUE = '1'

/** Mark setup as just-completed so the dashboard shows the guidance card. */
export function markFirstRunPending(): void {
  try {
    window.localStorage.setItem(FIRST_RUN_STORAGE_KEY, FIRST_RUN_FLAG_VALUE)
  } catch {
    // localStorage may be disabled (private mode); the guidance card simply
    // won't surface. Setup completion still proceeds.
  }
}

/** Whether the first-run flag is currently set. */
export function readFirstRunFlag(): boolean {
  try {
    return window.localStorage.getItem(FIRST_RUN_STORAGE_KEY) === FIRST_RUN_FLAG_VALUE
  } catch {
    return false
  }
}

/** Clear the first-run flag once the guidance card has been shown. */
export function clearFirstRunFlag(): void {
  try {
    window.localStorage.removeItem(FIRST_RUN_STORAGE_KEY)
  } catch {
    // Nothing to clear if storage is unavailable.
  }
}
