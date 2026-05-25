import * as settingsApi from '@/api/endpoints/settings'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import {
  CURRENCY_PATTERN,
  DEFAULT_CURRENCY,
  deriveCurrency,
  fetchAllSettingsEntries,
} from './concurrency'
import type {
  SettingsGet,
  SettingsSet,
  SettingsState,
} from './types'

const log = createLogger('settings')

async function fetchCurrencyImpl(set: SettingsSet): Promise<void> {
  try {
    const entries = await settingsApi.getNamespaceSettings('budget')
    const currencyEntry = entries.find((e) => e.definition.key === 'currency')
    if (!currencyEntry?.value) {
      log.warn('No currency value in budget settings, keeping default')
      return
    }
    if (!CURRENCY_PATTERN.test(currencyEntry.value)) {
      log.warn('Invalid currency value, keeping default', {
        value: sanitizeForLog(currencyEntry.value),
      })
      return
    }
    set({ currency: currencyEntry.value })
  } catch (error) {
    log.warn('Failed to fetch currency, keeping default', {
      error: sanitizeForLog(getErrorMessage(error)),
    })
  }
}

async function fetchSettingsDataImpl(
  set: SettingsSet,
  get: SettingsGet,
): Promise<void> {
  set({ loading: true, error: null })
  try {
    const [schemaResult, entriesResult] = await Promise.allSettled([
      settingsApi.getSchema(),
      fetchAllSettingsEntries(),
    ])
    const schema = schemaResult.status === 'fulfilled'
      ? schemaResult.value
      : get().schema
    const entries = entriesResult.status === 'fulfilled'
      ? entriesResult.value
      : get().entries
    const errors: string[] = []
    if (schemaResult.status === 'rejected') {
      errors.push(`Schema: ${getErrorMessage(schemaResult.reason)}`)
    }
    if (entriesResult.status === 'rejected') {
      errors.push(`Settings: ${getErrorMessage(entriesResult.reason)}`)
    }
    const patch: Partial<SettingsState> = {
      schema,
      entries,
      loading: false,
      error: errors.length > 0 ? errors.join('; ') : null,
    }
    patch.currency = deriveCurrency(entries) ?? DEFAULT_CURRENCY
    set(patch)
  } catch (error) {
    set({ loading: false, error: getErrorMessage(error) })
  }
}

async function refreshEntriesImpl(
  set: SettingsSet,
  get: SettingsGet,
): Promise<void> {
  // Skip if saves are in progress to avoid overwriting fresh data.
  if (get().savingKeys.size > 0) return
  // Capture the entries-generation BEFORE the fetch so a
  // ``resetSetting`` / ``updateSetting`` that completes during the
  // fetch window cannot have its result clobbered by this older
  // snapshot. ``savingKeys`` alone only catches mutations still in
  // flight at apply time; the generation check covers the
  // start-during-fetch / finish-before-apply race.
  const generationAtFetchStart = get().entriesGeneration
  let entries: Awaited<ReturnType<typeof fetchAllSettingsEntries>>
  try {
    entries = await fetchAllSettingsEntries()
  } catch (error) {
    // Surface the failure on the store's error slot only if this
    // snapshot is still relevant (no concurrent save mutated the
    // generation); otherwise drop silently so a stale failure does
    // not overwrite a fresher save's error state.
    if (
      get().savingKeys.size === 0
      && get().entriesGeneration === generationAtFetchStart
    ) {
      set({ error: getErrorMessage(error) })
    }
    return
  }
  // Re-check: a save / reset may have started OR completed during
  // the fetch. Either condition means this snapshot may be stale.
  if (
    get().savingKeys.size > 0
    || get().entriesGeneration !== generationAtFetchStart
  ) {
    return
  }
  const patch: Partial<SettingsState> = { entries, error: null }
  patch.currency = deriveCurrency(entries) ?? DEFAULT_CURRENCY
  set(patch)
}

export function createFetchActions(set: SettingsSet, get: SettingsGet) {
  return {
    fetchCurrency: () => fetchCurrencyImpl(set),
    fetchSettingsData: () => fetchSettingsDataImpl(set, get),
    refreshEntries: () => refreshEntriesImpl(set, get),
  }
}
