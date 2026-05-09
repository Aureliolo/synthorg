import { create } from 'zustand'

import { paginateAll } from '@/api/client'
import * as settingsApi from '@/api/endpoints/settings'
import type { SettingDefinition, SettingEntry, SettingNamespace } from '@/api/types/settings'
import type { WsEvent } from '@/api/types/websocket'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { useToastStore } from '@/stores/toast'

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

/**
 * Per-key in-flight refcount. Two concurrent ``updateSetting`` /
 * ``resetSetting`` calls on the same composite key need to be tracked
 * independently so the second call does not see the map empty when
 * the first one drains -- a ``Set<string>`` would collapse them and
 * weaken the post-reset anti-clobber guard. Map immutability is
 * preserved by cloning on every mutation; the value is the active
 * count (always >= 1 while present).
 */
function incrementSavingKey(
  current: ReadonlyMap<string, number>,
  key: string,
): Map<string, number> {
  const next = new Map(current)
  next.set(key, (next.get(key) ?? 0) + 1)
  return next
}

function decrementSavingKey(
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
 * higher-token result that already landed, so an older request whose
 * response arrives after the newer one cannot stamp stale data on
 * ``state.entries``. Backend-issued mutation IDs would be the
 * canonical solution; this local counter is a best-effort guard
 * until that contract exists.
 */
let _nextMutationToken = 0

function nextMutationToken(): number {
  _nextMutationToken += 1
  return _nextMutationToken
}

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
  /**
   * Composite keys ("ns/key") with their in-flight save count.
   *
   * A refcount Map (rather than a Set) so two concurrent saves on the
   * same composite key are tracked independently: when the first one
   * drains, the key stays in the map with count 1, and ``size > 0``
   * still reports "saves in flight" so ``refreshEntries()`` and the
   * post-reset snapshot guard do not race a stale snapshot over the
   * still-pending in-flight result.
   */
  savingKeys: ReadonlyMap<string, number>
  /**
   * Highest applied mutation token per composite key. Used to drop
   * out-of-order ``updateSetting`` responses: if our token is less
   * than the value here, a newer mutation already landed and we must
   * not overwrite ``state.entries`` with stale data. The map is
   * monotonic per key (only ever raised) so an entry never disappears
   * once seen; ``size`` grows with the number of distinct keys
   * mutated in this session, which is bounded by the schema.
   */
  appliedMutationTokens: ReadonlyMap<string, number>
  /**
   * Monotonic counter incremented on every successful save that
   * mutates ``entries``. ``resetSetting`` captures the value before
   * its post-reset refetch and rechecks it after; if a concurrent
   * save completed during the fetch (counter advanced), the
   * refetched snapshot may be stale and is discarded the same way
   * a refetch failure would be -- preventing the refetch from
   * silently overwriting the UI with a value that missed the
   * concurrent write.
   */
  entriesGeneration: number
  /** Error from the most recent save attempt. */
  saveError: string | null

  /** Fetch the configured currency from the budget settings namespace. */
  fetchCurrency: () => Promise<void>
  /** Fetch both schema and all settings entries. */
  fetchSettingsData: () => Promise<void>
  /** Lightweight re-fetch of entries only (for polling). */
  refreshEntries: () => Promise<void>
  /**
   * Update a single setting value.
   *
   * Follows the store-CRUD contract: never throws. Returns the updated
   * entry on success, ``null`` on failure (after logging and emitting
   * an error toast). Callers MUST NOT wrap in try/catch; check the
   * return value for ``null`` instead.
   */
  updateSetting: (
    ns: SettingNamespace,
    key: string,
    value: string,
  ) => Promise<SettingEntry | null>
  /**
   * Reset a setting to its default value.
   *
   * Returns ``true`` only when the server-side reset succeeds AND the
   * follow-up refetch is applied to ``state.entries``. Returns
   * ``false`` for ANY stale-local-view outcome: the server-side
   * reset itself failed (error toast emitted), the post-reset
   * refetch failed, OR the refetch succeeded but was intentionally
   * discarded because ``hasOtherSaves`` / ``generationDrifted``
   * left the snapshot at risk of clobbering a concurrent write
   * (warning toast emitted in those cases so the user knows the
   * local view is stale). Callers MUST NOT wrap in try/catch;
   * check the return value instead so they can keep dirty state
   * intact when the local view did not catch up.
   */
  resetSetting: (ns: SettingNamespace, key: string) => Promise<boolean>
  /** Handle a WebSocket event on the system channel. */
  updateFromWsEvent: (event: WsEvent) => void
}

export const useSettingsStore = create<SettingsState>()((set, get) => ({
  currency: DEFAULT_CURRENCY,
  schema: [],
  entries: [],
  loading: false,
  error: null,
  savingKeys: new Map(),
  appliedMutationTokens: new Map(),
  entriesGeneration: 0,
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
      patch.currency = deriveCurrency(entries) ?? DEFAULT_CURRENCY
      set(patch)
    } catch (error) {
      set({ loading: false, error: getErrorMessage(error) })
    }
  },

  refreshEntries: async () => {
    // Skip if saves are in progress to avoid overwriting fresh data.
    if (get().savingKeys.size > 0) return
    // Capture the entries-generation BEFORE the fetch so a
    // ``resetSetting`` / ``updateSetting`` that completes during the
    // fetch window cannot have its result clobbered by this older
    // snapshot. ``savingKeys`` alone only catches mutations still in
    // flight at apply time; the generation check covers the
    // start-during-fetch / finish-before-apply race.
    const generationAtFetchStart = get().entriesGeneration
    // Let errors propagate to usePolling's error tracking
    const entries = await fetchAllSettingsEntries()
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
  },

  updateSetting: async (ns, key, value) => {
    const compositeKey = `${ns}/${key}`
    // Capture the mutation token BEFORE the API call so that two
    // concurrent saves on the same key receive distinct, ordered
    // tokens. The apply branch below refuses to overwrite a
    // higher-token result that already landed, preventing an older
    // request whose response arrives after the newer one from
    // stamping stale data on ``state.entries``.
    const mutationToken = nextMutationToken()
    set((state) => ({
      savingKeys: incrementSavingKey(state.savingKeys, compositeKey),
      saveError: null,
    }))
    try {
      const updated = await settingsApi.updateSetting(ns, key, { value })
      // ``applied`` lifts out of the ``set`` callback so the
      // post-set toast / return-value branches can distinguish the
      // out-of-order drop path (entries left untouched, response
      // discarded) from the canonical success path. Without this
      // signal, callers using the ``null``-sentinel contract could
      // clear dirty state for a mutation the store explicitly
      // discarded.
      let applied = false
      set((state) => {
        const newSaving = decrementSavingKey(state.savingKeys, compositeKey)
        const lastApplied = state.appliedMutationTokens.get(compositeKey) ?? 0
        if (mutationToken <= lastApplied) {
          // A newer mutation already landed and overwrote (or
          // chose not to overwrite) ``entries``; this older response
          // must not regress ``state.entries`` to a stale value.
          // Still drain the savingKeys refcount and clear saveError
          // so the lifecycle accounting stays correct.
          log.debug('Dropping out-of-order updateSetting response', {
            compositeKey: sanitizeForLog(compositeKey),
            ourToken: mutationToken,
            lastApplied,
          })
          return { savingKeys: newSaving, saveError: null }
        }
        applied = true
        const hasExisting = state.entries.some(
          (e) => e.definition.namespace === ns && e.definition.key === key,
        )
        const newEntries = hasExisting
          ? state.entries.map((e) =>
              e.definition.namespace === ns && e.definition.key === key
                ? updated
                : e,
            )
          : [...state.entries, updated]
        const newApplied = new Map(state.appliedMutationTokens)
        newApplied.set(compositeKey, mutationToken)
        const patch: Partial<SettingsState> = {
          entries: newEntries,
          savingKeys: newSaving,
          appliedMutationTokens: newApplied,
          // Bump the entries-generation counter so a concurrent
          // ``resetSetting`` refetch in flight can detect that a
          // save landed during its fetch window and refuse to
          // overwrite the snapshot with stale data. See the
          // ``resetSetting`` finally block for the pair check.
          entriesGeneration: state.entriesGeneration + 1,
        }
        // Keep the standalone currency field in sync with the entry list.
        if (ns === 'budget' && key === 'currency') {
          patch.currency = deriveCurrency(newEntries) ?? DEFAULT_CURRENCY
        }
        return patch
      })
      if (!applied) {
        // Out-of-order response was dropped; the caller should NOT
        // see a success signal that lets it clear dirty state for
        // a mutation we never wrote to ``state.entries``. Return
        // the ``null`` sentinel to match the documented contract.
        return null
      }
      // Success toast per the store-CRUD contract. Batch callers
      // (saveSettingsBatch, CeremonyPolicyPage) intentionally do NOT
      // emit a separate aggregated success toast on top -- the store
      // already fires one per mutation, so an aggregated toast would
      // double up. The trade-off is N toasts on a batch save vs.
      // contract drift; the convention picks contract drift as the
      // worse failure mode.
      useToastStore.getState().add({
        variant: 'success',
        title: `Updated ${sanitizeForLog(compositeKey)}`,
      })
      return updated
    } catch (error) {
      const errorMessage = getErrorMessage(error)
      // Symmetric to the success-path drop: if a newer mutation has
      // already landed for this composite key, an older (failed)
      // response must not surface a "Failed to update" toast or
      // overwrite ``saveError`` -- the user's view shows the newer
      // value, so the failure of the superseded request is no
      // longer actionable. Drain the savingKeys refcount and
      // return the null sentinel without the noisy toast / log.
      const lastApplied = get().appliedMutationTokens.get(compositeKey) ?? 0
      if (mutationToken <= lastApplied) {
        set((state) => ({
          savingKeys: decrementSavingKey(state.savingKeys, compositeKey),
        }))
        return null
      }
      log.error('Update setting failed', {
        compositeKey: sanitizeForLog(compositeKey),
        error: sanitizeForLog(errorMessage),
      })
      set((state) => ({
        savingKeys: decrementSavingKey(state.savingKeys, compositeKey),
        saveError: errorMessage,
      }))
      // Error toast lives in the store so callers stop wrapping
      // mutation calls in try/catch. Bulk-save callers
      // (saveSettingsBatch) suppress their own aggregated error
      // toast when the per-call toast already fired; see
      // pages/settings/utils.ts for the dedupe handling.
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(error, `Failed to update ${sanitizeForLog(compositeKey)}`),
        description: sanitizeForLog(errorMessage),
      })
      return null
    }
  },

  resetSetting: async (ns, key) => {
    const compositeKey = `${ns}/${key}`
    set((state) => ({
      savingKeys: incrementSavingKey(state.savingKeys, compositeKey),
      saveError: null,
    }))
    try {
      await settingsApi.resetSetting(ns, key)
    } catch (error) {
      const errorMessage = getErrorMessage(error)
      log.error('Reset setting failed', {
        compositeKey: sanitizeForLog(compositeKey),
        error: sanitizeForLog(errorMessage),
      })
      set((state) => ({
        savingKeys: decrementSavingKey(state.savingKeys, compositeKey),
        saveError: errorMessage,
      }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(error, `Failed to reset ${sanitizeForLog(compositeKey)}`),
        description: sanitizeForLog(errorMessage),
      })
      return false
    }
    // Reset succeeded -- refetch entries to get the resolved default.
    let refreshedEntries: SettingEntry[] | undefined
    let refreshFailed = false
    let hasOtherSaves = false
    let generationDrifted = false
    // Capture the entries-generation before the fetch. Any
    // concurrent ``updateSetting`` that completes between this
    // capture and the apply branch below increments the counter,
    // so the post-fetch comparison detects "a save landed during
    // the fetch window" and refuses to overwrite ``state.entries``
    // with a snapshot that missed the concurrent write.
    // ``hasOtherSaves`` (size of ``savingKeys``) only catches
    // mutations still in flight at apply time; this generation
    // check covers the start-during-fetch / finish-before-apply
    // race that ``hasOtherSaves`` would otherwise miss.
    const generationAtFetchStart = get().entriesGeneration
    try {
      refreshedEntries = await fetchAllSettingsEntries()
    } catch (err) {
      // Reset applied but refetch failed -- UI is stale until next poll cycle
      refreshFailed = true
      log.warn('Post-reset refetch failed; data will refresh at next poll', {
        error: sanitizeForLog(getErrorMessage(err)),
      })
    } finally {
      set((state) => {
        const newSaving = decrementSavingKey(state.savingKeys, compositeKey)
        // ``hasOtherSaves`` lifts to the outer scope so the toast +
        // return path below treat a concurrent-save skip the same as
        // a refetch failure: the local view did not catch up either
        // way, and callers following the documented
        // "true == fully applied" contract must not clear dirty
        // state on a stale snapshot. ``newSaving.size > 0`` after
        // our decrement covers both the same-key concurrent case
        // (refcount > 1 before our decrement) and the different-key
        // case (other entries in the map).
        hasOtherSaves = newSaving.size > 0
        // Generation drift = a save completed during the fetch
        // window. Same outcome as ``hasOtherSaves``: do not
        // overwrite ``entries`` with the (now-stale) snapshot.
        generationDrifted = state.entriesGeneration !== generationAtFetchStart
        const update: Partial<SettingsState> = {
          savingKeys: newSaving,
        }
        if (refreshedEntries && !hasOtherSaves && !generationDrifted) {
          update.entries = refreshedEntries
          update.error = null
          // Bump the generation counter so a concurrent
          // ``refreshEntries`` (poll / WS) that started before this
          // apply but finishes afterward refuses to overwrite the
          // post-reset entries with its older snapshot. Mirrors the
          // ``updateSetting`` success-branch bump.
          update.entriesGeneration = state.entriesGeneration + 1
          if (ns === 'budget' && key === 'currency') {
            update.currency = deriveCurrency(refreshedEntries) ?? DEFAULT_CURRENCY
          }
        } else if (refreshFailed || hasOtherSaves || generationDrifted) {
          update.error =
            'Settings were reset, but the updated values could not be reloaded yet.'
        }
        return update
      })
    }
    // Return false on refresh failure, concurrent-save skip, or
    // generation drift so callers can keep dirty state and avoid
    // clearing UI on stale data; the reset itself succeeded
    // server-side, but the local view did not catch up. Callers
    // that need the literal "server-side mutation succeeded"
    // signal can inspect ``saveError`` instead.
    const localViewStale = refreshFailed || hasOtherSaves || generationDrifted
    if (localViewStale) {
      useToastStore.getState().add({
        variant: 'warning',
        title: `Reset ${sanitizeForLog(compositeKey)}`,
        description:
          'Reset succeeded, but the updated values could not be reloaded yet.'
          + ' They will appear after the next refresh.',
      })
    } else {
      useToastStore.getState().add({
        variant: 'success',
        title: `Reset ${sanitizeForLog(compositeKey)}`,
      })
    }
    return !localViewStale
  },

  updateFromWsEvent: (event) => {
    if (event.channel === 'system') {
      void get().refreshEntries().catch((err) => {
        log.warn('WebSocket-triggered refresh failed', {
          error: sanitizeForLog(getErrorMessage(err)),
        })
      })
    }
  },
}))
