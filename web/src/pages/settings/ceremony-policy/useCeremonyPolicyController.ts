import { useCallback, useEffect, useMemo, useState } from 'react'
import type {
  CeremonyPolicyConfig,
  CeremonyStrategyType,
  VelocityCalcType,
} from '@/api/types/ceremony-policy'
import type { Department } from '@/api/types/org'
import { useSettingsStore } from '@/stores/settings'
import { useSettingsData } from '@/hooks/useSettingsData'
import { useCeremonyPolicyStore } from '@/stores/ceremony-policy'
import { useToastStore } from '@/stores/toast'
import { STRATEGY_DEFAULT_VELOCITY_CALC } from '@/utils/constants'
import {
  type CeremonySnapshot,
  buildCeremonySnapshot,
  buildOverridesSnapshot,
  deriveCeremonyNames,
  fetchAllDepartments,
} from './ceremony-policy-helpers'

type AddToast = ReturnType<typeof useToastStore.getState>['add']

export interface CeremonyFormState {
  strategy: CeremonyStrategyType
  strategyConfig: Record<string, unknown>
  velocityCalculator: VelocityCalcType
  autoTransition: boolean
  transitionThreshold: number
}

function snapshotToForm(s: CeremonySnapshot): CeremonyFormState {
  return {
    strategy: s.strategy,
    strategyConfig: s.strategyConfig,
    velocityCalculator: s.velocityCalculator,
    autoTransition: s.autoTransition,
    transitionThreshold: s.transitionThreshold,
  }
}

interface CeremonyForm {
  form: CeremonyFormState
  setStrategyConfig: (config: Record<string, unknown>) => void
  setVelocityCalculator: (value: VelocityCalcType) => void
  setAutoTransition: (value: boolean) => void
  setTransitionThreshold: (value: number) => void
  handleStrategyChange: (strategy: CeremonyStrategyType) => void
}

function useCeremonyForm(
  snapshot: CeremonySnapshot,
  isDirty: boolean,
  setIsDirty: (dirty: boolean) => void,
): CeremonyForm {
  const [form, setForm] = useState<CeremonyFormState>(() => snapshotToForm(snapshot))

  // Re-sync from settings unless the user has unsaved local edits.
  useEffect(() => {
    if (isDirty) return
    // eslint-disable-next-line @eslint-react/set-state-in-effect -- legitimate external-store sync
    setForm(snapshotToForm(snapshot))
  }, [isDirty, snapshot])

  const update = useCallback(
    <K extends keyof CeremonyFormState>(key: K, value: CeremonyFormState[K]) => {
      setForm((prev) => ({ ...prev, [key]: value }))
      setIsDirty(true)
    },
    [setIsDirty],
  )

  // Strategy change also resets the velocity calculator to the default.
  const handleStrategyChange = useCallback(
    (s: CeremonyStrategyType) => {
      setForm((prev) => ({ ...prev, strategy: s, velocityCalculator: STRATEGY_DEFAULT_VELOCITY_CALC[s] }))
      setIsDirty(true)
    },
    [setIsDirty],
  )

  return {
    form,
    setStrategyConfig: useCallback((c: Record<string, unknown>) => update('strategyConfig', c), [update]),
    setVelocityCalculator: useCallback((v: VelocityCalcType) => update('velocityCalculator', v), [update]),
    setAutoTransition: useCallback((b: boolean) => update('autoTransition', b), [update]),
    setTransitionThreshold: useCallback((t: number) => update('transitionThreshold', t), [update]),
    handleStrategyChange,
  }
}

interface CeremonyOverrides {
  overrides: Record<string, CeremonyPolicyConfig | null>
  ceremonyNames: string[]
  handleOverrideChange: (name: string, policy: CeremonyPolicyConfig | null) => void
}

function useCeremonyOverrides(
  entries: ReturnType<typeof useSettingsStore.getState>['entries'],
  isDirty: boolean,
  setIsDirty: (dirty: boolean) => void,
): { overrides: CeremonyOverrides; overridesParseError: boolean } {
  const snapshot = useMemo(() => buildOverridesSnapshot(entries), [entries])
  const [overrides, setOverrides] = useState(snapshot.overrides)

  useEffect(() => {
    if (isDirty) return
    // eslint-disable-next-line @eslint-react/set-state-in-effect -- legitimate external-store sync
    setOverrides(snapshot.overrides)
  }, [isDirty, snapshot.overrides])

  const handleOverrideChange = useCallback(
    (name: string, policy: CeremonyPolicyConfig | null) => {
      setOverrides((prev) => {
        const next = { ...prev }
        if (policy === null) Reflect.deleteProperty(next, name)
        else next[name] = policy
        return next
      })
      setIsDirty(true)
    },
    [setIsDirty],
  )

  const ceremonyNames = useMemo(() => deriveCeremonyNames(overrides), [overrides])

  return {
    overrides: { overrides, ceremonyNames, handleOverrideChange },
    overridesParseError: snapshot.overridesParseError,
  }
}

function useDepartmentsList(addToast: AddToast): { departments: readonly Department[]; deptLoading: boolean } {
  const [departments, setDepartments] = useState<readonly Department[]>([])
  const [deptLoading, setDeptLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      const result = await fetchAllDepartments()
      if (cancelled) return
      setDepartments(result.departments)
      setDeptLoading(false)
      if (result.cycleDetected) {
        addToast({
          variant: 'warning',
          title: 'Department list may be incomplete',
          description: 'Pagination cursor cycle detected. Refresh the page to retry.',
        })
      } else if (result.failed) {
        addToast({ variant: 'error', title: 'Failed to load departments' })
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [addToast])

  return { departments, deptLoading }
}

type UpdateSetting = ReturnType<typeof useSettingsStore.getState>['updateSetting']

async function saveCeremonySettings(
  updateSetting: UpdateSetting,
  form: CeremonyFormState,
  overrides: Record<string, CeremonyPolicyConfig | null>,
): Promise<number> {
  // Each key is an independent PUT; the settings service has no batch
  // update. `allSettled` is defence-in-depth against a future regression
  // that breaks updateSetting's no-throw store-CRUD contract.
  const settled = await Promise.allSettled([
    updateSetting('coordination', 'ceremony_strategy', form.strategy),
    updateSetting('coordination', 'ceremony_strategy_config', JSON.stringify(form.strategyConfig)),
    updateSetting('coordination', 'ceremony_velocity_calculator', form.velocityCalculator),
    updateSetting('coordination', 'ceremony_auto_transition', String(form.autoTransition)),
    updateSetting('coordination', 'ceremony_transition_threshold', String(form.transitionThreshold)),
    updateSetting('coordination', 'ceremony_policy_overrides', JSON.stringify(overrides)),
  ])
  return settled.filter((r) => r.status === 'rejected' || r.value == null).length
}

function useParseErrorToasts(addToast: AddToast, configParseError: boolean, overridesParseError: boolean): void {
  useEffect(() => {
    if (configParseError) {
      addToast({ variant: 'warning', title: 'Failed to parse ceremony_strategy_config setting' })
    }
  }, [configParseError, addToast])
  useEffect(() => {
    if (overridesParseError) {
      addToast({ variant: 'warning', title: 'Failed to parse ceremony_policy_overrides setting' })
    }
  }, [overridesParseError, addToast])
}

export interface CeremonyPolicyController {
  form: CeremonyForm
  overrides: CeremonyOverrides
  departments: readonly Department[]
  deptLoading: boolean
  store: {
    resolvedPolicy: ReturnType<typeof useCeremonyPolicyStore.getState>['resolvedPolicy']
    activeStrategy: ReturnType<typeof useCeremonyPolicyStore.getState>['activeStrategy']
    loading: boolean
    storeError: string | null
    activeStrategyError: string | null
    storeSaveError: string | null
  }
  configParseError: boolean
  overridesParseError: boolean
  saving: boolean
  saveError: string | null
  isDirty: boolean
  handleSave: () => Promise<void>
}

export function useCeremonyPolicyController(): CeremonyPolicyController {
  const addToast = useToastStore((s) => s.add)
  useSettingsData()
  const entries = useSettingsStore((s) => s.entries)
  const updateSetting = useSettingsStore((s) => s.updateSetting)

  const resolvedPolicy = useCeremonyPolicyStore((s) => s.resolvedPolicy)
  const activeStrategy = useCeremonyPolicyStore((s) => s.activeStrategy)
  const loading = useCeremonyPolicyStore((s) => s.loading)
  const storeError = useCeremonyPolicyStore((s) => s.error)
  const activeStrategyError = useCeremonyPolicyStore((s) => s.activeStrategyError)
  const storeSaveError = useCeremonyPolicyStore((s) => s.saveError)
  const fetchResolvedPolicy = useCeremonyPolicyStore((s) => s.fetchResolvedPolicy)
  const fetchActiveStrategy = useCeremonyPolicyStore((s) => s.fetchActiveStrategy)

  const [isDirty, setIsDirty] = useState(false)
  const snapshot = useMemo(() => buildCeremonySnapshot(entries), [entries])
  const form = useCeremonyForm(snapshot, isDirty, setIsDirty)
  const { overrides, overridesParseError } = useCeremonyOverrides(entries, isDirty, setIsDirty)
  const { departments, deptLoading } = useDepartmentsList(addToast)

  useParseErrorToasts(addToast, snapshot.configParseError, overridesParseError)

  useEffect(() => {
    void fetchResolvedPolicy()
    void fetchActiveStrategy()
  }, [fetchResolvedPolicy, fetchActiveStrategy])

  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const handleSave = useCallback(async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const failedCount = await saveCeremonySettings(updateSetting, form.form, overrides.overrides)
      if (failedCount > 0) {
        setSaveError(`${failedCount} ceremony setting(s) failed to save`)
        return
      }
      setIsDirty(false)
      void fetchResolvedPolicy()
    } finally {
      setSaving(false)
    }
  }, [updateSetting, form.form, overrides.overrides, fetchResolvedPolicy])

  return {
    form,
    overrides,
    departments,
    deptLoading,
    store: { resolvedPolicy, activeStrategy, loading, storeError, activeStrategyError, storeSaveError },
    configParseError: snapshot.configParseError,
    overridesParseError,
    saving,
    saveError,
    isDirty,
    handleSave,
  }
}
