import { create } from 'zustand'

import { paginateAll } from '@/api/client'
import * as settingsApi from '@/api/endpoints/settings'
import type { SettingDefinition, SettingEntry, SettingNamespace } from '@/api/types/settings'
import type { WsEvent } from '@/api/types/websocket'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'

const log = createLogger('settings')

/** Page size used by every settings list call in this store. */
const SETTINGS_PAGE_LIMIT = 200

/** Walk every page of the settings list endpoint into a single array. */
function fetchAllSettingsEntries(): Promise<SettingEntry[]> {
  return paginateAll<SettingEntry>((cursor) =>
    settingsApi.getAllSettings({ cursor, limit: SETTINGS_PAGE_LIMIT }),
  )
}

const CURRENCY_PATTERN = /^[A-Z]{3}$/

/** Extract valid currency from entries, or undefined if not found/invalid. */
function deriveCurrency(
  entries: SettingEntry[],
): string | undefined {
  const entry = entries.find(
    (e) => e.definition.namespace === 'budget'
      && e.definition.key === 'currency',
  )
  if (entry?.value && CURRENCY_PATTERN.test(entry.value)) {
    return entry.value
  }
  return undefined
}

interface SettingsState {
  /** ISO 4217 currency code for display formatting. */
  currency: string
  /** Full setting definitions (schema). */
  schema: SettingDefinition[]
  /** All setting entries with resolved values. */
  entries: SettingEntry[]
  /** Whether the initial fetch is in progress. */
  loading: boolean
  /** Error from the most recent fetch. */
  error: string | null
  /** Composite keys ("ns/key") currently being saved. */
  savingKeys: ReadonlySet<string>
  /** Error from the most recent save attempt. */
  saveError: string | null

  /** Fetch the configured currency from the budget settings namespace. */
  fetchCurrency: () => Promise<void>
  /** Fetch both schema and all settings entries. */
  fetchSettingsData: () => Promise<void>
  /** Lightweight re-fetch of entries only (for polling). */
  refreshEntries: () => Promise<void>
  /** Update a single setting value. Returns the updated entry on success. */
  updateSetting: (ns: SettingNamespace, key: string, value: string) => Promise<SettingEntry>
  /** Reset a setting to its default value. */
  resetSetting: (ns: SettingNamespace, key: string) => Promise<void>
  /** Handle a WebSocket event on the system channel. */
  updateFromWsEvent: (event: WsEvent) => void
}

export const useSettingsStore = create<SettingsState>()((set, get) => ({
  currency: DEFAULT_CURRENCY,
  schema: [],
  entries: [],
  loading: false,
  error: null,
  savingKeys: new Set(),
  saveError: null,

  fetchCurrency: async () => {
    try {
      const entries = await settingsApi.getNamespaceSettings('budget')
      const currencyEntry = entries.find((e) => e.definition.key === 'currency')
      if (!currencyEntry?.value) {
        log.warn('No currency value in budget settings, keeping default')
        return
      }
      if (!CURRENCY_PATTERN.test(currencyEntry.value)) {
        log.warn('Invalid currency value, keeping default:', currencyEntry.value)
        return
      }
      set({ currency: currencyEntry.value })
    } catch (error) {
      log.warn(
        'Failed to fetch currency, keeping default:',
        getErrorMessage(error),
      )
    }
  },

  fetchSettingsData: async () => {
    set({ loading: true, error: null })
    try {
      const [schemaResult, entriesResult] = await Promise.allSettled([
        settingsApi.getSchema(),
        fetchAllSettingsEntries(),
      ])
      const schema = schemaResult.status === 'fulfilled' ? schemaResult.value : get().schema
      const entries = entriesResult.status === 'fulfilled' ? entriesResult.value : get().entries
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
      const c = deriveCurrency(entries)
      if (c) patch.currency = c
      set(patch)
    } catch (error) {
      set({ loading: false, error: getErrorMessage(error) })
    }
  },

  refreshEntries: async () => {
    // Skip if saves are in progress to avoid overwriting fresh data
    if (get().savingKeys.size > 0) return
    // Let errors propagate to usePolling's error tracking
    const entries = await fetchAllSettingsEntries()
    // Re-check: a save may have started during the fetch
    if (get().savingKeys.size > 0) return
    const patch: Partial<SettingsState> = { entries, error: null }
    const c = deriveCurrency(entries)
    if (c) patch.currency = c
    set(patch)
  },

  updateSetting: async (ns, key, value) => {
    const compositeKey = `${ns}/${key}`
    set((state) => ({
      savingKeys: new Set([...state.savingKeys, compositeKey]),
      saveError: null,
    }))
    try {
      const updated = await settingsApi.updateSetting(ns, key, { value })
      set((state) => {
        const newEntries = state.entries.map((e) =>
          e.definition.namespace === ns && e.definition.key === key ? updated : e,
        )
        const newSaving = new Set(state.savingKeys)
        newSaving.delete(compositeKey)
        const patch: Partial<SettingsState> = {
          entries: newEntries,
          savingKeys: newSaving,
        }
        // Keep the standalone currency field in sync with the entry list.
        if (ns === 'budget' && key === 'currency') {
          const c = deriveCurrency(newEntries)
          if (c) patch.currency = c
        }
        return patch
      })
      return updated
    } catch (error) {
      set((state) => {
        const newSaving = new Set(state.savingKeys)
        newSaving.delete(compositeKey)
        return { savingKeys: newSaving, saveError: getErrorMessage(error) }
      })
      throw error
    }
  },

  resetSetting: async (ns, key) => {
    const compositeKey = `${ns}/${key}`
    set((state) => ({
      savingKeys: new Set([...state.savingKeys, compositeKey]),
      saveError: null,
    }))
    try {
      await settingsApi.resetSetting(ns, key)
    } catch (error) {
      set((state) => {
        const newSaving = new Set(state.savingKeys)
        newSaving.delete(compositeKey)
        return { savingKeys: newSaving, saveError: getErrorMessage(error) }
      })
      throw error
    }
    // Reset succeeded -- refetch entries to get the resolved default.
    let refreshedEntries: SettingEntry[] | undefined
    try {
      refreshedEntries = await fetchAllSettingsEntries()
    } catch (err) {
      // Reset applied but refetch failed -- UI is stale until next poll cycle
      log.warn('Post-reset refetch failed; data will refresh at next poll', err)
    } finally {
      set((state) => {
        const newSaving = new Set(state.savingKeys)
        newSaving.delete(compositeKey)
        const update: Partial<SettingsState> = {
          savingKeys: newSaving,
        }
        if (refreshedEntries) {
          update.entries = refreshedEntries
          update.error = null
          if (ns === 'budget' && key === 'currency') {
            const c = deriveCurrency(refreshedEntries)
            if (c) update.currency = c
          }
        }
        return update
      })
    }
  },

  updateFromWsEvent: (event) => {
    if (event.channel === 'system') {
      void get().refreshEntries().catch((err) => {
        log.warn('WebSocket-triggered refresh failed:', getErrorMessage(err))
      })
    }
  },
}))
