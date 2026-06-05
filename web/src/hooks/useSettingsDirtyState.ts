import { useCallback, useMemo, useRef, useState } from 'react'
import type { SettingEntry, SettingNamespace } from '@/api/types/settings'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { saveSettingsBatch } from '@/pages/settings/utils'

const log = createLogger('useSettingsDirtyState')

interface RunBatchSaveArgs {
  readonly dirtyValues: Map<string, string>
  readonly updateSetting: (
    ns: SettingNamespace,
    key: string,
    value: string,
  ) => Promise<unknown>
  readonly setDirtyValues: React.Dispatch<React.SetStateAction<Map<string, string>>>
}

/**
 * Run one batch-save pass: persist every pending edit, then prune the
 * dirty map for the keys that landed cleanly. Failures are passed
 * back via the `failedKeys` returned by `saveSettingsBatch`; those
 * stay in the dirty map for retry. No aggregate success/failure toast
 * fires here because the store fires one toast per mutation per the
 * CRUD contract.
 */
async function _runBatchSave(args: RunBatchSaveArgs): Promise<void> {
  const pending = new Map(args.dirtyValues)
  const failedKeys = await saveSettingsBatch(pending, args.updateSetting)
  args.setDirtyValues((prev) => {
    const next = new Map(prev)
    for (const [key, value] of pending) {
      if (!failedKeys.has(key) && next.get(key) === value) {
        next.delete(key)
      }
    }
    return next
  })
}

/**
 * Defence-in-depth fallback: per-mutation failures are tracked via
 * `failedKeys` and toasted at the store layer, so this only fires on
 * programming errors, network failures in the batch coordinator, or
 * exceptions in the state updater. Log + toast so the user sees
 * feedback and the dirty state stays intact for retry.
 */
function _onBatchSaveError(error: unknown, dirtyValues: Map<string, string>): void {
  log.error('Unexpected error during batch save', {
    pendingKeys: Array.from(dirtyValues.keys()).map(sanitizeForLog),
    error: sanitizeForLog(getErrorMessage(error)),
  })
  useToastStore.getState().add({
    variant: 'error',
    title: 'Save failed',
    description: 'Some settings could not be saved. Please try again.',
  })
}

export interface UseSettingsDirtyStateReturn {
  dirtyValues: Map<string, string>
  setDirtyValues: React.Dispatch<
    React.SetStateAction<Map<string, string>>
  >
  handleValueChange: (ck: string, value: string) => void
  handleDiscard: () => void
  handleSave: () => Promise<void>
  persistedValues: ReadonlyMap<string, string>
}

export function useSettingsDirtyState(
  entries: SettingEntry[],
  updateSetting: (
    ns: SettingNamespace,
    key: string,
    value: string,
  ) => Promise<unknown>,
): UseSettingsDirtyStateReturn {
  const [dirtyValues, setDirtyValues] = useState<Map<string, string>>(
    () => new Map(),
  )

  const persistedValues = useMemo(
    () =>
      new Map(
        entries.map((entry) => [
          `${entry.definition.namespace}/${entry.definition.key}`,
          entry.value,
        ]),
      ),
    [entries],
  )

  const handleValueChange = useCallback(
    (compositeKey: string, value: string) => {
      setDirtyValues((prev) => {
        const next = new Map(prev)
        if (persistedValues.get(compositeKey) === value) {
          next.delete(compositeKey)
        } else {
          next.set(compositeKey, value)
        }
        return next
      })
    },
    [persistedValues],
  )

  const handleDiscard = useCallback(() => {
    setDirtyValues(new Map())
  }, [])

  const isSavingRef = useRef(false)
  const handleSave = useCallback(async () => {
    if (isSavingRef.current) return
    isSavingRef.current = true
    try {
      await _runBatchSave({ dirtyValues, updateSetting, setDirtyValues })
    } catch (error) {
      _onBatchSaveError(error, dirtyValues)
    } finally {
      isSavingRef.current = false
    }
  }, [dirtyValues, updateSetting])

  return {
    dirtyValues,
    setDirtyValues,
    handleValueChange,
    handleDiscard,
    handleSave,
    persistedValues,
  }
}
