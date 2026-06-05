import { useCallback, useEffect, useState } from 'react'
import { Building2, ChevronDown, ChevronRight } from 'lucide-react'
import type { CeremonyPolicyConfig, CeremonyStrategyType } from '@/api/types/ceremony-policy'
import type { Department } from '@/api/types/org'
import { InheritToggle } from '@/components/ui/inherit-toggle'
import { SectionCard } from '@/components/ui/section-card'
import { useCeremonyPolicyStore } from '@/stores/ceremony-policy'
import { CEREMONY_STRATEGY_LABELS, STRATEGY_DEFAULT_VELOCITY_CALC } from '@/utils/constants'
import { cn } from '@/lib/utils'
import { StrategyPicker } from './StrategyPicker'
import { StrategyConfigPanel } from './StrategyConfigPanel'
import { PolicyFieldsPanel } from './PolicyFieldsPanel'

export interface DepartmentOverridesPanelProps {
  departments: readonly Department[]
}

function derivePolicyFields(policy: CeremonyPolicyConfig | null | undefined, strategy: CeremonyStrategyType) {
  const p: Partial<CeremonyPolicyConfig> = policy ?? {}
  return {
    strategyConfig: (p.strategy_config ?? {}),
    velocityCalculator: p.velocity_calculator ?? STRATEGY_DEFAULT_VELOCITY_CALC[strategy],
    autoTransition: p.auto_transition ?? true,
    transitionThreshold: p.transition_threshold ?? 1.0,
  }
}

interface DeptPolicyState {
  saving: boolean
  departmentError: string | undefined
  isEditing: boolean
  effectivePolicy: CeremonyPolicyConfig | null | undefined
  strategy: CeremonyStrategyType
  handleInheritChange: (inherit: boolean) => void
  handleStrategyChange: (strategy: CeremonyStrategyType) => void
  handlePolicyFieldChange: (field: keyof CeremonyPolicyConfig, value: unknown) => void
}

function useDepartmentPolicy(deptName: string): DeptPolicyState {
  const policy = useCeremonyPolicyStore((s) => s.departmentPolicies.get(deptName))
  const fetchPolicy = useCeremonyPolicyStore((s) => s.fetchDepartmentPolicy)
  const updatePolicy = useCeremonyPolicyStore((s) => s.updateDepartmentPolicy)
  const clearPolicy = useCeremonyPolicyStore((s) => s.clearDepartmentPolicy)
  const saving = useCeremonyPolicyStore((s) => s.saving)
  const departmentError = useCeremonyPolicyStore((s) => s.departmentErrors.get(deptName))

  useEffect(() => {
    void fetchPolicy(deptName)
  }, [deptName, fetchPolicy])

  const hasOverride = policy != null && Object.keys(policy).length > 0
  // Local draft defers the API call until the user sets a strategy/field.
  const [localDraft, setLocalDraft] = useState<CeremonyPolicyConfig | null>(null)
  const isEditing = hasOverride || localDraft != null
  const effectivePolicy = policy ?? localDraft

  const handleInheritChange = useCallback(
    (inherit: boolean) => {
      if (inherit) {
        setLocalDraft(null)
        void clearPolicy(deptName)
      } else {
        setLocalDraft(policy ?? {})
      }
    },
    [deptName, clearPolicy, policy],
  )

  const handleStrategyChange = useCallback(
    (s: CeremonyStrategyType) => {
      void updatePolicy(deptName, { ...effectivePolicy, strategy: s })
      setLocalDraft(null)
    },
    [deptName, effectivePolicy, updatePolicy],
  )

  const handlePolicyFieldChange = useCallback(
    (field: keyof CeremonyPolicyConfig, value: unknown) => {
      void updatePolicy(deptName, { ...effectivePolicy, [field]: value })
      setLocalDraft(null)
    },
    [deptName, effectivePolicy, updatePolicy],
  )

  return {
    saving,
    departmentError,
    isEditing,
    effectivePolicy,
    strategy: effectivePolicy?.strategy ?? 'task_driven',
    handleInheritChange,
    handleStrategyChange,
    handlePolicyFieldChange,
  }
}

interface DepartmentOverrideBodyProps {
  effectivePolicy: CeremonyPolicyConfig | null | undefined
  strategy: CeremonyStrategyType
  saving: boolean
  onStrategyChange: (strategy: CeremonyStrategyType) => void
  onFieldChange: (field: keyof CeremonyPolicyConfig, value: unknown) => void
}

function DepartmentOverrideBody({
  effectivePolicy,
  strategy,
  saving,
  onStrategyChange,
  onFieldChange,
}: DepartmentOverrideBodyProps) {
  const fields = derivePolicyFields(effectivePolicy, strategy)
  return (
    <div className={cn('space-y-3 pl-2 border-l-2 border-accent/20')}>
      <StrategyPicker value={strategy} onChange={onStrategyChange} disabled={saving} />
      <StrategyConfigPanel
        strategy={strategy}
        config={fields.strategyConfig}
        onChange={(c) => onFieldChange('strategy_config', c)}
        disabled={saving}
      />
      <PolicyFieldsPanel
        velocityCalculator={fields.velocityCalculator}
        autoTransition={fields.autoTransition}
        transitionThreshold={fields.transitionThreshold}
        onVelocityCalculatorChange={(v) => onFieldChange('velocity_calculator', v)}
        onAutoTransitionChange={(v) => onFieldChange('auto_transition', v)}
        onTransitionThresholdChange={(v) => onFieldChange('transition_threshold', v)}
        disabled={saving}
      />
    </div>
  )
}

function DepartmentRow({ dept }: { dept: Department }) {
  const [expanded, setExpanded] = useState(false)
  const p = useDepartmentPolicy(dept.name)
  const Chevron = expanded ? ChevronDown : ChevronRight

  return (
    <div className="border-b border-border last:border-b-0">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-card/50"
      >
        <Chevron className="size-3.5 text-text-muted" />
        <span className="flex-1 text-sm font-medium">{dept.name}</span>
        <span className="text-xs text-text-muted">
          {p.isEditing ? CEREMONY_STRATEGY_LABELS[p.strategy] : 'Inherit'}
        </span>
      </button>

      {expanded && (
        <div className="space-y-3 px-3 pb-3">
          {p.departmentError && <p className="text-xs text-danger">{p.departmentError}</p>}
          <InheritToggle inherit={!p.isEditing} onChange={p.handleInheritChange} disabled={p.saving} />
          {p.isEditing && (
            <DepartmentOverrideBody
              effectivePolicy={p.effectivePolicy}
              strategy={p.strategy}
              saving={p.saving}
              onStrategyChange={p.handleStrategyChange}
              onFieldChange={p.handlePolicyFieldChange}
            />
          )}
        </div>
      )}
    </div>
  )
}

export function DepartmentOverridesPanel({ departments }: DepartmentOverridesPanelProps) {
  // Store-wide saveError shown once at the panel level (only one department
  // save runs at a time, so a single banner is sufficient).
  const saveError = useCeremonyPolicyStore((s) => s.saveError)

  if (departments.length === 0) {
    return (
      <p className="text-xs text-text-secondary">
        No departments configured. Department overrides will appear here once departments are added.
      </p>
    )
  }

  return (
    <SectionCard title="Department Overrides" icon={Building2}>
      {saveError && <p className="mb-2 text-xs text-danger">Save failed: {saveError}</p>}
      <div className="divide-y divide-border rounded-md border border-border">
        {departments.map((dept) => (
          <DepartmentRow key={dept.name} dept={dept} />
        ))}
      </div>
    </SectionCard>
  )
}
