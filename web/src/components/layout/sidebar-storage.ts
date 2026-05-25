/** localStorage helpers for the sidebar's user-collapsed preference. */

export const STORAGE_KEY = 'sidebar_collapsed'

export function readCollapsed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

export function writeCollapsed(value: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(value))
  } catch {
    // Ignore: storage may be unavailable (e.g. quota exceeded).
  }
}
