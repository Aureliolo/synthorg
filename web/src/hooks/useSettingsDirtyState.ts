import { useCallback, useMemo, useRef, useState } from 'react'
import type { SettingEntry, SettingNamespace } from '@/api/types/settings'
import { saveSettingsBatch } from '@/pages/settings/utils'

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
  ) => Promise<unknown | null>,
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
      const pending = new Map(dirtyValues)
      const failedKeys = await saveSettingsBatch(
        pending,
        updateSetting,
      )

      setDirtyValues((prev) => {
        const next = new Map(prev)
        for (const [key, value] of pending) {
          if (
            !failedKeys.has(key) &&
            next.get(key) === value
          ) {
            next.delete(key)
          }
        }
        return next
      })

      // No aggregate toast (success or failure) on batch saves: the
      // store fires one toast per mutation per the CRUD contract,
      // so an aggregate at this level would just stack on top.
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
