import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SettingEntry, SettingNamespace } from '@/api/types/settings'
import { useSettingsStore } from '@/stores/settings'
import { useAnimationPreset } from '@/hooks/useAnimationPreset'
import { useSettingsData } from '@/hooks/useSettingsData'
import { useSettingsDirtyState } from '@/hooks/useSettingsDirtyState'
import { useSettingsKeyboard } from '@/hooks/useSettingsKeyboard'
import { useUnsavedChangesGuard } from '@/hooks/use-unsaved-changes-guard'
import {
  NAMESPACE_ORDER,
  SETTINGS_ADVANCED_KEY,
  SETTINGS_ADVANCED_WARNED_KEY,
} from '@/utils/constants'
import { buildControllerDisabledMap, saveSettingsBatch } from './utils'
import {
  buildCodeEntries,
  computeChangedKeys,
  countRestartRequired,
  filterByNamespace,
  snapshotEntries,
} from './settings-page-helpers'

export type ViewMode = 'gui' | 'code'

function useChangedKeys(entries: SettingEntry[]): Set<string> {
  const prevEntriesRef = useRef<Map<string, string>>(new Map())
  const changedKeys = useMemo(() => computeChangedKeys(entries, prevEntriesRef.current), [entries])
  // Update the ref after the render commits (not inside the memo, to
  // respect concurrent rendering).
  useEffect(() => {
    prevEntriesRef.current = snapshotEntries(entries)
  }, [entries])
  return changedKeys
}

interface SettingsUiState {
  searchQuery: string
  setSearchQuery: (value: string) => void
  viewMode: ViewMode
  setViewMode: (value: ViewMode) => void
  codeDirty: boolean
  setCodeDirty: (value: boolean) => void
  showCodeDiscardWarning: boolean
  setShowCodeDiscardWarning: (value: boolean) => void
  restartBannerCount: number
  setRestartBannerCount: (value: number) => void
  activeNamespace: SettingNamespace | null
  setActiveNamespace: (value: SettingNamespace | null) => void
}

function useSettingsUiState(): SettingsUiState {
  const [searchQuery, setSearchQuery] = useState('')
  const [viewMode, setViewMode] = useState<ViewMode>('gui')
  const [codeDirty, setCodeDirty] = useState(false)
  const [showCodeDiscardWarning, setShowCodeDiscardWarning] = useState(false)
  const [restartBannerCount, setRestartBannerCount] = useState(0)
  const [activeNamespace, setActiveNamespace] = useState<SettingNamespace | null>(null)
  return {
    searchQuery,
    setSearchQuery,
    viewMode,
    setViewMode,
    codeDirty,
    setCodeDirty,
    showCodeDiscardWarning,
    setShowCodeDiscardWarning,
    restartBannerCount,
    setRestartBannerCount,
    activeNamespace,
    setActiveNamespace,
  }
}

interface AdvancedMode {
  advancedMode: boolean
  showAdvancedWarning: boolean
  setShowAdvancedWarning: (value: boolean) => void
  handleAdvancedToggle: (checked: boolean) => void
  confirmAdvancedMode: () => void
  disableAdvanced: () => void
}

function useAdvancedMode(
  entries: SettingEntry[],
  setDirtyValues: ReturnType<typeof useSettingsDirtyState>['setDirtyValues'],
): AdvancedMode {
  const [advancedMode, setAdvancedMode] = useState(
    () => localStorage.getItem(SETTINGS_ADVANCED_KEY) === 'true',
  )
  const [showAdvancedWarning, setShowAdvancedWarning] = useState(false)

  const pruneAdvancedDrafts = useCallback(() => {
    setDirtyValues((prev) => {
      const next = new Map(prev)
      for (const ck of prev.keys()) {
        const entry = entries.find((e) => `${e.definition.namespace}/${e.definition.key}` === ck)
        if (entry?.definition.level === 'advanced') next.delete(ck)
      }
      return next
    })
  }, [entries, setDirtyValues])

  const setAdvanced = useCallback((value: boolean) => {
    setAdvancedMode(value)
    localStorage.setItem(SETTINGS_ADVANCED_KEY, String(value))
  }, [])

  const handleAdvancedToggle = useCallback(
    (checked: boolean) => {
      if (checked && sessionStorage.getItem(SETTINGS_ADVANCED_WARNED_KEY) !== 'true') {
        setShowAdvancedWarning(true)
        return
      }
      if (!checked) pruneAdvancedDrafts()
      setAdvanced(checked)
    },
    [pruneAdvancedDrafts, setAdvanced],
  )

  const confirmAdvancedMode = useCallback(() => {
    sessionStorage.setItem(SETTINGS_ADVANCED_WARNED_KEY, 'true')
    setAdvanced(true)
    setShowAdvancedWarning(false)
  }, [setAdvanced])

  const disableAdvanced = useCallback(() => {
    pruneAdvancedDrafts()
    setAdvanced(false)
  }, [pruneAdvancedDrafts, setAdvanced])

  return {
    advancedMode,
    showAdvancedWarning,
    setShowAdvancedWarning,
    handleAdvancedToggle,
    confirmAdvancedMode,
    disableAdvanced,
  }
}

export interface SettingsFilters {
  filteredByNamespace: Map<SettingNamespace, SettingEntry[]>
  namespaceCounts: Map<SettingNamespace, number>
  effectiveNamespace: SettingNamespace | null
}

/** Namespace-grouped + search-filtered entries, with the active-namespace gate. */
function useSettingsFilters(
  entries: SettingEntry[],
  advancedMode: boolean,
  searchQuery: string,
  activeNamespace: SettingNamespace | null,
): SettingsFilters {
  const filteredByNamespace = useMemo(
    () => filterByNamespace(entries, advancedMode, searchQuery),
    [entries, advancedMode, searchQuery],
  )
  const namespaceCounts = useMemo(
    () => new Map(NAMESPACE_ORDER.map((ns) => [ns, filteredByNamespace.get(ns)?.length ?? 0])),
    [filteredByNamespace],
  )
  const effectiveNamespace =
    activeNamespace && (namespaceCounts.get(activeNamespace) ?? 0) > 0 ? activeNamespace : null
  return { filteredByNamespace, namespaceCounts, effectiveNamespace }
}

export interface SettingsPageController {
  data: ReturnType<typeof useSettingsData>
  ui: SettingsUiState
  advanced: AdvancedMode
  filters: SettingsFilters
  dirtyValues: ReturnType<typeof useSettingsDirtyState>['dirtyValues']
  handleValueChange: ReturnType<typeof useSettingsDirtyState>['handleValueChange']
  handleDiscard: ReturnType<typeof useSettingsDirtyState>['handleDiscard']
  storeSavingKeys: ReturnType<typeof useSettingsStore.getState>['savingKeys']
  anim: ReturnType<typeof useAnimationPreset>
  searchRef: React.RefObject<{ focus: () => void } | null>
  changedKeys: Set<string>
  controllerDisabledMap: ReturnType<typeof buildControllerDisabledMap>
  codeEntries: SettingEntry[]
  unsavedGuard: ReturnType<typeof useUnsavedChangesGuard>
  handleSave: () => Promise<void>
  handleCodeSave: (changes: Map<string, string>) => Promise<Set<string>>
}

interface CodeSaveDeps {
  updateSetting: ReturnType<typeof useSettingsData>['updateSetting']
  entries: SettingEntry[]
  setDirtyValues: ReturnType<typeof useSettingsDirtyState>['setDirtyValues']
  setRestartBannerCount: (value: number) => void
}

/** Persist a batch of code-editor changes, prune saved drafts, surface restarts. */
async function runCodeSave(changes: Map<string, string>, deps: CodeSaveDeps): Promise<Set<string>> {
  const failedKeys = await saveSettingsBatch(changes, deps.updateSetting)
  deps.setDirtyValues((prev) => {
    const next = new Map(prev)
    for (const key of changes.keys()) {
      if (!failedKeys.has(key)) next.delete(key)
    }
    return next
  })
  const restartCount = countRestartRequired(changes.keys(), deps.entries, failedKeys)
  if (restartCount > 0) deps.setRestartBannerCount(restartCount)
  return failedKeys
}

export function useSettingsPageController(): SettingsPageController {
  const data = useSettingsData()
  const storeSavingKeys = useSettingsStore((s) => s.savingKeys)
  const anim = useAnimationPreset()
  const ui = useSettingsUiState()
  const searchRef = useRef<{ focus: () => void }>(null)

  const changedKeys = useChangedKeys(data.entries)
  const { dirtyValues, setDirtyValues, handleValueChange, handleDiscard, handleSave: baseSave } =
    useSettingsDirtyState(data.entries, data.updateSetting)
  const advanced = useAdvancedMode(data.entries, setDirtyValues)
  const { setRestartBannerCount } = ui

  const handleSave = useCallback(async () => {
    const restartCount = countRestartRequired(dirtyValues.keys(), data.entries)
    await baseSave()
    if (restartCount > 0) setRestartBannerCount(restartCount)
  }, [baseSave, dirtyValues, data.entries, setRestartBannerCount])

  useSettingsKeyboard({
    onSave: () => void handleSave(),
    onSearchFocus: () => searchRef.current?.focus(),
    canSave: dirtyValues.size > 0 && !data.saving,
  })

  const unsavedGuard = useUnsavedChangesGuard({
    when: dirtyValues.size > 0 || ui.codeDirty,
    message: 'You have unsaved setting changes. Leaving now will discard them. Continue anyway?',
  })

  const filters = useSettingsFilters(
    data.entries,
    advanced.advancedMode,
    ui.searchQuery,
    ui.activeNamespace,
  )

  const controllerDisabledMap = useMemo(
    () => buildControllerDisabledMap(data.entries, dirtyValues),
    [data.entries, dirtyValues],
  )

  const handleCodeSave = useCallback(
    (changes: Map<string, string>): Promise<Set<string>> =>
      runCodeSave(changes, {
        updateSetting: data.updateSetting,
        entries: data.entries,
        setDirtyValues,
        setRestartBannerCount,
      }),
    [data.updateSetting, data.entries, setDirtyValues, setRestartBannerCount],
  )

  const codeEntries = useMemo(
    () => buildCodeEntries(data.entries, dirtyValues, advanced.advancedMode),
    [data.entries, dirtyValues, advanced.advancedMode],
  )

  return {
    data,
    ui,
    advanced,
    filters,
    dirtyValues,
    handleValueChange,
    handleDiscard,
    storeSavingKeys,
    anim,
    searchRef,
    changedKeys,
    controllerDisabledMap,
    codeEntries,
    unsavedGuard,
    handleSave,
    handleCodeSave,
  }
}
