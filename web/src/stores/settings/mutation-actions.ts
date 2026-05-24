import * as settingsApi from '@/api/endpoints/settings'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type {
  SettingEntry,
  SettingNamespace,
} from '@/api/types/settings'
import {
  DEFAULT_CURRENCY,
  decrementSavingKey,
  deriveCurrency,
  fetchAllSettingsEntries,
  incrementSavingKey,
  nextMutationToken,
} from './concurrency'
import type {
  SettingsGet,
  SettingsSet,
  SettingsState,
} from './types'

const log = createLogger('settings')

interface UpdateApplyArgs {
  ns: SettingNamespace
  key: string
  updated: SettingEntry
  compositeKey: string
  mutationToken: number
}

function applyUpdatedEntry(
  state: SettingsState,
  args: UpdateApplyArgs,
): Partial<SettingsState> {
  const { ns, key, updated, compositeKey, mutationToken } = args
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
    savingKeys: decrementSavingKey(state.savingKeys, compositeKey),
    appliedMutationTokens: newApplied,
    // Bump the entries-generation counter so a concurrent
    // ``resetSetting`` refetch in flight can detect that a save
    // landed during its fetch window and refuse to overwrite the
    // snapshot with stale data.
    entriesGeneration: state.entriesGeneration + 1,
  }
  if (ns === 'budget' && key === 'currency') {
    patch.currency = deriveCurrency(newEntries) ?? DEFAULT_CURRENCY
  }
  return patch
}

function emitOutOfOrderDrop(
  state: SettingsState,
  compositeKey: string,
  mutationToken: number,
  lastApplied: number,
): Partial<SettingsState> {
  log.debug('Dropping out-of-order updateSetting response', {
    compositeKey: sanitizeForLog(compositeKey),
    ourToken: mutationToken,
    lastApplied,
  })
  return {
    savingKeys: decrementSavingKey(state.savingKeys, compositeKey),
    saveError: null,
  }
}

async function updateSettingImpl(
  set: SettingsSet,
  get: SettingsGet,
  ns: SettingNamespace,
  key: string,
  value: string,
): Promise<SettingEntry | null> {
  const compositeKey = `${ns}/${key}`
  const mutationToken = nextMutationToken()
  set((state) => ({
    savingKeys: incrementSavingKey(state.savingKeys, compositeKey),
    saveError: null,
  }))
  try {
    const updated = await settingsApi.updateSetting(ns, key, { value })
    let applied = false
    set((state) => {
      const lastApplied = state.appliedMutationTokens.get(compositeKey) ?? 0
      if (mutationToken <= lastApplied) {
        return emitOutOfOrderDrop(state, compositeKey, mutationToken, lastApplied)
      }
      applied = true
      return applyUpdatedEntry(state, {
        ns,
        key,
        updated,
        compositeKey,
        mutationToken,
      })
    })
    if (!applied) return null
    useToastStore.getState().add({
      variant: 'success',
      title: `Updated ${sanitizeForLog(compositeKey)}`,
    })
    return updated
  } catch (error) {
    return handleUpdateError(set, get, error, compositeKey, mutationToken)
  }
}

function handleUpdateError(
  set: SettingsSet,
  get: SettingsGet,
  error: unknown,
  compositeKey: string,
  mutationToken: number,
): null {
  const errorMessage = getErrorMessage(error)
  // Symmetric to the success-path drop: if a newer mutation has
  // already landed for this composite key, an older (failed)
  // response must not surface a "Failed to update" toast or
  // overwrite ``saveError`` -- the user's view shows the newer
  // value, so the failure of the superseded request is no longer
  // actionable.
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
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(error, `Failed to update ${sanitizeForLog(compositeKey)}`),
    description: sanitizeForLog(errorMessage),
  })
  return null
}

interface ResetRefetchOutcome {
  refreshedEntries: SettingEntry[] | undefined
  refreshFailed: boolean
}

async function fetchAfterReset(): Promise<ResetRefetchOutcome> {
  try {
    return { refreshedEntries: await fetchAllSettingsEntries(), refreshFailed: false }
  } catch (err) {
    log.warn('Post-reset refetch failed; data will refresh at next poll', {
      error: sanitizeForLog(getErrorMessage(err)),
    })
    return { refreshedEntries: undefined, refreshFailed: true }
  }
}

interface ApplyResetOutcome {
  hasOtherSaves: boolean
  generationDrifted: boolean
}

interface ResetApplyArgs {
  ns: SettingNamespace
  key: string
  compositeKey: string
  refreshedEntries: SettingEntry[] | undefined
  refreshFailed: boolean
  generationAtFetchStart: number
}

function applyFreshSnapshot(
  state: SettingsState,
  args: ResetApplyArgs,
  refreshedEntries: SettingEntry[],
): Partial<SettingsState> {
  const update: Partial<SettingsState> = {
    entries: refreshedEntries,
    error: null,
    entriesGeneration: state.entriesGeneration + 1,
  }
  if (args.ns === 'budget' && args.key === 'currency') {
    update.currency = deriveCurrency(refreshedEntries) ?? DEFAULT_CURRENCY
  }
  return update
}

function buildResetPatch(
  state: SettingsState,
  args: ResetApplyArgs,
  outcome: ApplyResetOutcome,
): Partial<SettingsState> {
  const newSaving = decrementSavingKey(state.savingKeys, args.compositeKey)
  const update: Partial<SettingsState> = { savingKeys: newSaving }
  const snapshotIsFresh = Boolean(
    args.refreshedEntries
    && !outcome.hasOtherSaves
    && !outcome.generationDrifted,
  )
  if (snapshotIsFresh && args.refreshedEntries) {
    Object.assign(update, applyFreshSnapshot(state, args, args.refreshedEntries))
  } else {
    update.error =
      'Settings were reset, but the updated values could not be reloaded yet.'
  }
  return update
}

function applyResetSnapshot(
  set: SettingsSet,
  args: ResetApplyArgs,
): ApplyResetOutcome {
  let outcome: ApplyResetOutcome = {
    hasOtherSaves: false,
    generationDrifted: false,
  }
  set((state) => {
    const newSaving = decrementSavingKey(state.savingKeys, args.compositeKey)
    outcome = {
      hasOtherSaves: newSaving.size > 0,
      generationDrifted:
        state.entriesGeneration !== args.generationAtFetchStart,
    }
    return buildResetPatch(state, args, outcome)
  })
  return outcome
}

async function resetSettingImpl(
  set: SettingsSet,
  get: SettingsGet,
  ns: SettingNamespace,
  key: string,
): Promise<boolean> {
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
  const generationAtFetchStart = get().entriesGeneration
  const { refreshedEntries, refreshFailed } = await fetchAfterReset()
  const { hasOtherSaves, generationDrifted } = applyResetSnapshot(set, {
    ns,
    key,
    compositeKey,
    refreshedEntries,
    refreshFailed,
    generationAtFetchStart,
  })
  const localViewStale = refreshFailed || hasOtherSaves || generationDrifted
  useToastStore.getState().add(
    localViewStale
      ? {
          variant: 'warning',
          title: `Reset ${sanitizeForLog(compositeKey)}`,
          description:
            'Reset succeeded, but the updated values could not be reloaded yet.'
            + ' They will appear after the next refresh.',
        }
      : {
          variant: 'success',
          title: `Reset ${sanitizeForLog(compositeKey)}`,
        },
  )
  return !localViewStale
}

export function createMutationActions(set: SettingsSet, get: SettingsGet) {
  return {
    updateSetting: (ns: SettingNamespace, key: string, value: string) =>
      updateSettingImpl(set, get, ns, key, value),
    resetSetting: (ns: SettingNamespace, key: string) =>
      resetSettingImpl(set, get, ns, key),
  }
}
