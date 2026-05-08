import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { ArrowLeft, Loader2, Settings, Timer } from 'lucide-react'
import type { CeremonyPolicyConfig, CeremonyStrategyType, VelocityCalcType } from '@/api/types/ceremony-policy'
import type { Department } from '@/api/types/org'
import { Button } from '@/components/ui/button'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonCard } from '@/components/ui/skeleton'
import { PolicySourceBadge } from '@/components/ui/policy-source-badge'
import { useSettingsStore } from '@/stores/settings'
import { useSettingsData } from '@/hooks/useSettingsData'
import { useCeremonyPolicyStore } from '@/stores/ceremony-policy'
import { useToastStore } from '@/stores/toast'
import { ROUTES } from '@/router/routes'
import { CEREMONY_STRATEGY_TYPES, STRATEGY_DEFAULT_VELOCITY_CALC, VELOCITY_CALC_TYPES } from '@/utils/constants'
import { StrategyPicker } from './StrategyPicker'
import { StrategyChangeWarning } from './StrategyChangeWarning'
import { StrategyConfigPanel } from './StrategyConfigPanel'
import { PolicyFieldsPanel } from './PolicyFieldsPanel'
import { DepartmentOverridesPanel } from './DepartmentOverridesPanel'
import { CeremonyListPanel } from './CeremonyListPanel'

export default function CeremonyPolicyPage() {
  const addToast = useToastStore((s) => s.add)

  // Ensure settings are fetched (handles deep-link arrival)
  useSettingsData()

  const settingsEntries = useSettingsStore((s) => s.entries)
  const updateSetting = useSettingsStore((s) => s.updateSetting)

  const resolvedPolicy = useCeremonyPolicyStore((s) => s.resolvedPolicy)
  const activeStrategy = useCeremonyPolicyStore((s) => s.activeStrategy)
  const loading = useCeremonyPolicyStore((s) => s.loading)
  const storeError = useCeremonyPolicyStore((s) => s.error)
  const activeStrategyError = useCeremonyPolicyStore((s) => s.activeStrategyError)
  const storeSaveError = useCeremonyPolicyStore((s) => s.saveError)
  const fetchResolvedPolicy = useCeremonyPolicyStore((s) => s.fetchResolvedPolicy)
  const fetchActiveStrategy = useCeremonyPolicyStore((s) => s.fetchActiveStrategy)

  // Derive initial values from settings entries
  const settingsSnapshot = useMemo(() => {
    const get = (key: string) => settingsEntries.find(
      (e) => e.definition.namespace === 'coordination' && e.definition.key === key,
    )?.value

    let config: Record<string, unknown> = {}
    let configParseError = false
    const sc = get('ceremony_strategy_config')
    if (sc) {
      try {
        const parsed: unknown = JSON.parse(sc)
        if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
          config = parsed as Record<string, unknown>
        } else {
          configParseError = true
        }
      } catch {
        configParseError = true
      }
    }

    const rawStrategy = get('ceremony_strategy') as string | undefined
    const strategy: CeremonyStrategyType = (
      rawStrategy && CEREMONY_STRATEGY_TYPES.includes(rawStrategy as CeremonyStrategyType)
        ? rawStrategy as CeremonyStrategyType
        : 'task_driven'
    )

    const rawThreshold = Number(get('ceremony_transition_threshold') ?? '1.0')
    const transitionThreshold = Number.isFinite(rawThreshold)
      ? Math.min(Math.max(rawThreshold, 0.01), 1.0)
      : 1.0

    return {
      strategy,
      strategyConfig: config,
      velocityCalculator: (() => {
        const raw = get('ceremony_velocity_calculator') as string | undefined
        if (raw && VELOCITY_CALC_TYPES.includes(raw as VelocityCalcType)) {
          return raw as VelocityCalcType
        }
        return STRATEGY_DEFAULT_VELOCITY_CALC[strategy]
      })(),
      autoTransition: (() => {
        const raw = get('ceremony_auto_transition') as string | undefined
        if (raw === undefined) return true
        return raw.toLowerCase() === 'true'
      })(),
      transitionThreshold,
      configParseError,
    }
  }, [settingsEntries])

  // Local form state for project-level policy (initialized from settings).
  // Consolidated into a single object to allow atomic re-sync from the
  // settings snapshot without triggering multiple setState calls.
  interface FormState {
    strategy: CeremonyStrategyType
    strategyConfig: Record<string, unknown>
    velocityCalculator: VelocityCalcType
    autoTransition: boolean
    transitionThreshold: number
  }
  const [form, setForm] = useState<FormState>(() => ({
    strategy: settingsSnapshot.strategy,
    strategyConfig: settingsSnapshot.strategyConfig,
    velocityCalculator: settingsSnapshot.velocityCalculator,
    autoTransition: settingsSnapshot.autoTransition,
    transitionThreshold: settingsSnapshot.transitionThreshold,
  }))
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  // Track whether the user has made unsaved edits.  Set to true on any
  // local form change, cleared on save or when the snapshot re-syncs
  // successfully (i.e. when the user has not touched the form).
  const [isDirty, setIsDirty] = useState(false)

  // Re-sync form when the underlying settings entries change (deep-link
  // fetch or WS update).  Skip when the user has unsaved local edits to
  // avoid clobbering in-progress work.
  useEffect(() => {
    if (isDirty) return
    // eslint-disable-next-line @eslint-react/set-state-in-effect -- legitimate external-store sync
    setForm({
      strategy: settingsSnapshot.strategy,
      strategyConfig: settingsSnapshot.strategyConfig,
      velocityCalculator: settingsSnapshot.velocityCalculator,
      autoTransition: settingsSnapshot.autoTransition,
      transitionThreshold: settingsSnapshot.transitionThreshold,
    })
  }, [
    isDirty,
    settingsSnapshot.strategy,
    settingsSnapshot.strategyConfig,
    settingsSnapshot.velocityCalculator,
    settingsSnapshot.autoTransition,
    settingsSnapshot.transitionThreshold,
  ])

  // Convenience destructuring for template readability
  const { strategy, strategyConfig, velocityCalculator, autoTransition, transitionThreshold } = form
  const setStrategy = useCallback((s: CeremonyStrategyType) => { setForm((prev) => ({ ...prev, strategy: s })); setIsDirty(true) }, [])
  const setStrategyConfig = useCallback((c: Record<string, unknown>) => { setForm((prev) => ({ ...prev, strategyConfig: c })); setIsDirty(true) }, [])
  const setVelocityCalculator = useCallback((v: VelocityCalcType) => { setForm((prev) => ({ ...prev, velocityCalculator: v })); setIsDirty(true) }, [])
  const setAutoTransition = useCallback((b: boolean) => { setForm((prev) => ({ ...prev, autoTransition: b })); setIsDirty(true) }, [])
  const setTransitionThreshold = useCallback((t: number) => { setForm((prev) => ({ ...prev, transitionThreshold: t })); setIsDirty(true) }, [])

  // Departments for the overrides panel
  const [departments, setDepartments] = useState<readonly Department[]>([])
  const [deptLoading, setDeptLoading] = useState(true)
  // deptLoadError tracking removed -- we show partial results via
  // departments.length > 0 and surface errors via toast.

  // Per-ceremony overrides (from ceremony_policy_overrides setting)
  const ceremonyOverridesSnapshot = useMemo(() => {
    const raw = settingsEntries.find(
      (e) => e.definition.namespace === 'coordination' && e.definition.key === 'ceremony_policy_overrides',
    )?.value
    let overridesParseError = false
    if (raw) {
      try {
        const parsed: unknown = JSON.parse(raw)
        if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
          return { overrides: parsed as Record<string, CeremonyPolicyConfig | null>, overridesParseError }
        }
        overridesParseError = true
      } catch {
        overridesParseError = true
      }
    }
    return { overrides: {} as Record<string, CeremonyPolicyConfig | null>, overridesParseError }
  }, [settingsEntries])
  const [ceremonyOverrides, setCeremonyOverrides] = useState<Record<string, CeremonyPolicyConfig | null>>(ceremonyOverridesSnapshot.overrides)

  // Re-sync ceremony overrides when the settings snapshot changes.
  // Skip when the user has unsaved edits to avoid clobbering in-progress work.
  useEffect(() => {
    if (isDirty) return
    // eslint-disable-next-line @eslint-react/set-state-in-effect -- legitimate external-store sync
    setCeremonyOverrides(ceremonyOverridesSnapshot.overrides)
  }, [isDirty, ceremonyOverridesSnapshot.overrides])

  // Show toasts for JSON parse failures (outside useMemo to avoid side effects in memos)
  useEffect(() => {
    if (settingsSnapshot.configParseError) {
      addToast({ variant: 'warning', title: 'Failed to parse ceremony_strategy_config setting' })
    }
  }, [settingsSnapshot.configParseError, addToast])

  useEffect(() => {
    if (ceremonyOverridesSnapshot.overridesParseError) {
      addToast({ variant: 'warning', title: 'Failed to parse ceremony_policy_overrides setting' })
    }
  }, [ceremonyOverridesSnapshot.overridesParseError, addToast])

  // Derive ceremony names from overrides + common ceremony names
  const ceremonyNames = useMemo(() => {
    const names = new Set(Object.keys(ceremonyOverrides))
    for (const name of ['sprint_planning', 'standup', 'sprint_review', 'retrospective']) {
      names.add(name)
    }
    return [...names].sort()
  }, [ceremonyOverrides])

  // Fetch resolved policy and active strategy on mount
  useEffect(() => {
    fetchResolvedPolicy()
    fetchActiveStrategy()
  }, [fetchResolvedPolicy, fetchActiveStrategy])

  // Fetch all departments (paginated) with error handling.
  // deptLoading is initialized to true, so we only need to clear it on
  // completion.  The set-state calls below are legitimate async callbacks.
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      const allDepts: Department[] = []
      try {
        const { listDepartments } = await import('@/api/endpoints/company')
        let cursor: string | null = null
        const limit = 200
        // Cycle guard: a malformed backend that returns the same
        // ``nextCursor`` twice with ``hasMore=true`` would otherwise
        // loop forever. Bail out with partial results when we see a
        // cursor repeat AND surface the truncation to the operator so
        // they can retry or report it -- silently dropping items is
        // the worst failure mode for a settings page backed by
        // server-side state.
        const seenCursors = new Set<string>()
        let cycleDetected = false
        while (!cancelled) {
          const result = await listDepartments({ cursor, limit })
          allDepts.push(...result.data)
          if (!result.hasMore || !result.nextCursor) break
          if (seenCursors.has(result.nextCursor)) {
            cycleDetected = true
            break
          }
          seenCursors.add(result.nextCursor)
          cursor = result.nextCursor
        }
        if (cycleDetected && !cancelled) {
          addToast({
            variant: 'warning',
            title: 'Department list may be incomplete',
            description:
              'Pagination cursor cycle detected -- refresh the page to retry.',
          })
        }
      } catch {
        if (!cancelled) {
          // Error surfaced via toast; partial results preserved in allDepts
          addToast({ variant: 'error', title: 'Failed to load departments' })
        }
      } finally {
        if (!cancelled) {
          setDepartments(allDepts)
          setDeptLoading(false)
        }
      }
    }
    load()
    return () => { cancelled = true }
  }, [addToast])

  // Save handler: persist all ceremony settings.
  // Individual calls are used because the settings service does not support
  // batch updates -- each key is an independent PUT /settings/{ns}/{key}.
  // ``updateSetting`` follows the store-CRUD contract: never throws,
  // returns ``null`` on failure (with the per-call error toast already
  // emitted by the store). We treat any ``null`` result as a partial
  // failure, keep the page dirty so the user can retry, and skip
  // ``fetchResolvedPolicy`` (the resolved view would race with the
  // half-saved state).
  const handleSave = useCallback(async () => {
    setSaving(true)
    setSaveError(null)
    const results = await Promise.all([
      updateSetting('coordination', 'ceremony_strategy', strategy),
      updateSetting('coordination', 'ceremony_strategy_config', JSON.stringify(strategyConfig)),
      updateSetting('coordination', 'ceremony_velocity_calculator', velocityCalculator),
      updateSetting('coordination', 'ceremony_auto_transition', String(autoTransition)),
      updateSetting('coordination', 'ceremony_transition_threshold', String(transitionThreshold)),
      updateSetting('coordination', 'ceremony_policy_overrides', JSON.stringify(ceremonyOverrides)),
    ])
    setSaving(false)
    if (results.includes(null)) {
      const failedCount = results.filter((r) => r === null).length
      setSaveError(`${failedCount} ceremony setting(s) failed to save`)
      return
    }
    setIsDirty(false)
    addToast({ variant: 'success', title: 'Ceremony policy saved' })
    fetchResolvedPolicy()
  }, [
    strategy, strategyConfig, velocityCalculator, autoTransition, transitionThreshold,
    ceremonyOverrides, updateSetting, addToast, fetchResolvedPolicy,
  ])

  // Handle per-ceremony override changes
  const handleCeremonyOverrideChange = useCallback(
    (name: string, policy: CeremonyPolicyConfig | null) => {
      setCeremonyOverrides((prev) => {
        const next = { ...prev }
        if (policy === null) {
          delete next[name]
        } else {
          next[name] = policy
        }
        return next
      })
      setIsDirty(true)
    },
    [],
  )

  // When strategy changes, update velocity calculator to the strategy default
  const handleStrategyChange = useCallback((s: CeremonyStrategyType) => {
    setStrategy(s)
    setVelocityCalculator(STRATEGY_DEFAULT_VELOCITY_CALC[s])
  }, [setStrategy, setVelocityCalculator])

  return (
    <ErrorBoundary level="page">
      <div className="mx-auto max-w-3xl space-y-section-gap p-6">
        {/* Header */}
        <div className="flex items-center gap-3">
          <Link
            to={ROUTES.SETTINGS}
            aria-label="Back to settings"
            className="rounded-md p-1.5 text-text-muted transition-colors hover:bg-card hover:text-foreground"
          >
            <ArrowLeft className="size-4" />
          </Link>
          <div className="flex items-center gap-2">
            <Timer className="size-5 text-accent" />
            <h1 className="text-lg font-semibold">Ceremony Policy</h1>
          </div>
        </div>

        {loading && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="size-6 animate-spin text-text-muted" />
          </div>
        )}

        {!loading && storeError && (
          <div className="rounded-md border border-danger/30 bg-danger/5 p-card text-sm text-danger">
            Failed to load resolved policy: {storeError}
          </div>
        )}

        {!loading && activeStrategyError && (
          <div className="rounded-md border border-warning/30 bg-warning/5 p-card text-sm text-warning">
            Failed to load active strategy: {activeStrategyError}
          </div>
        )}

        {!loading && (
          <>
            {/* Strategy change warning */}
            {activeStrategy?.strategy && strategy !== activeStrategy.strategy && (
              <StrategyChangeWarning
                currentStrategy={strategy}
                activeStrategy={activeStrategy.strategy}
              />
            )}

            {/* Project-level policy */}
            <SectionCard title="Project Policy" icon={Settings}>
              <div className="space-y-5">
                <div className="flex items-start gap-2">
                  <div className="flex-1">
                    <StrategyPicker
                      value={strategy}
                      onChange={handleStrategyChange}
                      disabled={saving}
                    />
                  </div>
                  {resolvedPolicy && (
                    <PolicySourceBadge source={resolvedPolicy.strategy.source} className="mt-7" />
                  )}
                </div>

                <div className="border-t border-border pt-4">
                  <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-text-muted">
                    Strategy Configuration
                  </p>
                  <StrategyConfigPanel
                    strategy={strategy}
                    config={strategyConfig}
                    onChange={setStrategyConfig}
                    disabled={saving}
                  />
                </div>

                <div className="border-t border-border pt-4">
                  <PolicyFieldsPanel
                    velocityCalculator={velocityCalculator}
                    autoTransition={autoTransition}
                    transitionThreshold={transitionThreshold}
                    onVelocityCalculatorChange={setVelocityCalculator}
                    onAutoTransitionChange={setAutoTransition}
                    onTransitionThresholdChange={setTransitionThreshold}
                    resolvedPolicy={resolvedPolicy}
                    disabled={saving}
                  />
                </div>

                {(storeSaveError || saveError) && (
                  <div className="rounded-md border border-danger/30 bg-danger/5 p-card text-sm text-danger">
                    Save failed: {saveError ?? storeSaveError}
                  </div>
                )}

                {(settingsSnapshot.configParseError || ceremonyOverridesSnapshot.overridesParseError) && (
                  <div className="rounded-md border border-warning/30 bg-warning/5 p-card text-sm text-warning">
                    Cannot save -- stored JSON is corrupt. Fix the raw values in the settings code editor before saving.
                  </div>
                )}

                <div className="flex items-center justify-end gap-3 pt-2">
                  {isDirty && !saving && (
                    <span className="text-xs text-text-muted">Unsaved changes</span>
                  )}
                  <Button
                    onClick={handleSave}
                    disabled={
                      !isDirty ||
                      saving ||
                      settingsSnapshot.configParseError ||
                      ceremonyOverridesSnapshot.overridesParseError
                    }
                  >
                    {saving && <Loader2 className="mr-2 size-4 animate-spin" />}
                    Save Policy
                  </Button>
                </div>
              </div>
            </SectionCard>

            {/* Department overrides */}
            {deptLoading && <SkeletonCard />}
            {!deptLoading && departments.length > 0 && (
              <DepartmentOverridesPanel departments={departments} />
            )}

            {/* Per-ceremony overrides */}
            <CeremonyListPanel
              overrides={ceremonyOverrides}
              ceremonyNames={ceremonyNames}
              onOverrideChange={handleCeremonyOverrideChange}
              saving={saving}
            />
          </>
        )}
      </div>
    </ErrorBoundary>
  )
}
