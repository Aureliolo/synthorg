import { paginateAll } from '@/api/client'
import * as settingsApi from '@/api/endpoints/settings'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import type { SettingEntry } from '@/api/types/settings'

/** Page size used by every settings list call in this store. */
const SETTINGS_PAGE_LIMIT = 200

/** Walk every page of the settings list endpoint into a single array. */
export function fetchAllSettingsEntries(): Promise<SettingEntry[]> {
  return paginateAll<SettingEntry>((cursor) =>
    settingsApi.getAllSettings({ cursor, limit: SETTINGS_PAGE_LIMIT }),
  )
}

export const CURRENCY_PATTERN = /^[A-Z]{3}$/

/**
 * Per-key in-flight refcount. Two concurrent ``updateSetting`` /
 * ``resetSetting`` calls on the same composite key need to be tracked
 * independently so the second call does not see the map empty when
 * the first one drains.
 */
export function incrementSavingKey(
  current: ReadonlyMap<string, number>,
  key: string,
): Map<string, number> {
  const next = new Map(current)
  next.set(key, (next.get(key) ?? 0) + 1)
  return next
}

export function decrementSavingKey(
  current: ReadonlyMap<string, number>,
  key: string,
): Map<string, number> {
  const next = new Map(current)
  const count = next.get(key) ?? 0
  if (count <= 1) {
    next.delete(key)
  } else {
    next.set(key, count - 1)
  }
  return next
}

/**
 * Module-local monotonic counter for ``updateSetting`` mutation
 * tokens. Two concurrent saves on the same composite key receive
 * distinct, ordered tokens; the apply branch refuses to overwrite a
 * higher-token result that already landed, preventing an older
 * request whose response arrives after the newer one from stamping
 * stale data on ``state.entries``. Backend-issued mutation IDs would
 * be the canonical solution; this local counter is a best-effort
 * guard until that contract exists.
 */
let _nextMutationToken = 0

export function nextMutationToken(): number {
  _nextMutationToken += 1
  return _nextMutationToken
}

/** Extract valid currency from entries, or undefined if not found/invalid. */
export function deriveCurrency(
  entries: SettingEntry[],
): string | undefined {
  const entry = entries.find(
    (e) =>
      e.definition.namespace === 'budget' && e.definition.key === 'currency',
  )
  if (entry?.value && CURRENCY_PATTERN.test(entry.value)) {
    return entry.value
  }
  return undefined
}

export { DEFAULT_CURRENCY }
