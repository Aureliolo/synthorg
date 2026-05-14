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

function DepartmentRow({ dept }: { dept: Department }) {
  const [expanded, setExpanded] = useState(false)
  const policy = useCeremonyPolicyStore((s) => s.departmentPolicies.get(dept.name))
  const fetchPolicy = useCeremonyPolicyStore((s) => s.fetchDepartmentPolicy)
  const updatePolicy = useCeremonyPolicyStore((s) => s.updateDepartmentPolicy)
  const clearPolicy = useCeremonyPolicyStore((s) => s.clearDepartmentPolicy)
  const saving = useCeremonyPolicyStore((s) => s.saving)
  const departmentError = useCeremonyPolicyStore((s) => s.departmentErrors.get(dept.name))

  useEffect(() => {
    void fetchPolicy(dept.name)
  }, [dept.name, fetchPolicy])

  const hasOverride = policy != null && Object.keys(policy).length > 0
  // Local draft for new overrides (defers API call until explicit save via strategy/field changes)
  const [localDraft, setLocalDraft] = useState<CeremonyPolicyConfig | null>(null)
  const isEditing = hasOverride || localDraft != null
  const effectivePolicy = policy ?? localDraft
  const strategy = effectivePolicy?.strategy ?? 'task_driven'

  const handleInheritChange = useCallback(
    (inherit: boolean) => {
      if (inherit) {
        setLocalDraft(null)
        void clearPolicy(dept.name)
      } else {
        // Seed from existing policy if available, otherwise empty override
        setLocalDraft(policy ?? {})
      }
    },
    [dept.name, clearPolicy, policy],
  )

  const handleStrategyChange = useCallback(
    (s: CeremonyStrategyType) => {
      const data = { ...effectivePolicy, strategy: s }
      void updatePolicy(dept.name, data)
      setLocalDraft(null)
    },
    [dept.name, effectivePolicy, updatePolicy],
  )

  const handlePolicyFieldChange = useCallback(
    (field: keyof CeremonyPolicyConfig, value: unknown) => {
      void updatePolicy(dept.name, { ...effectivePolicy, [field]: value })
      setLocalDraft(null)
    },
    [dept.name, effectivePolicy, updatePolicy],
  )

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
          {isEditing
            ? CEREMONY_STRATEGY_LABELS[strategy as CeremonyStrategyType]
            : 'Inherit'}
        </span>
      </button>

      {expanded && (
        <div className="space-y-3 px-3 pb-3">
          {departmentError && (
            <p className="text-xs text-danger">{departmentError}</p>
          )}

          <InheritToggle
            inherit={!isEditing}
            onChange={handleInheritChange}
            disabled={saving}
          />

          {isEditing && (
            <div className={cn('space-y-3 pl-2 border-l-2 border-accent/20')}>
              <StrategyPicker
                value={strategy as CeremonyStrategyType}
                onChange={handleStrategyChange}
                disabled={saving}
              />
              <StrategyConfigPanel
                strategy={strategy as CeremonyStrategyType}
                config={(effectivePolicy?.strategy_config ?? {}) as Record<string, unknown>}
                onChange={(c) => handlePolicyFieldChange('strategy_config', c)}
                disabled={saving}
              />
              <PolicyFieldsPanel
                velocityCalculator={effectivePolicy?.velocity_calculator ?? STRATEGY_DEFAULT_VELOCITY_CALC[effectivePolicy?.strategy ?? 'task_driven']}
                autoTransition={effectivePolicy?.auto_transition ?? true}
                transitionThreshold={effectivePolicy?.transition_threshold ?? 1.0}
                onVelocityCalculatorChange={(v) => handlePolicyFieldChange('velocity_calculator', v)}
                onAutoTransitionChange={(v) => handlePolicyFieldChange('auto_transition', v)}
                onTransitionThresholdChange={(v) => handlePolicyFieldChange('transition_threshold', v)}
                disabled={saving}
              />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function DepartmentOverridesPanel({ departments }: DepartmentOverridesPanelProps) {
  // Store-wide saveError displayed once at the panel level (only one department
  // save runs at a time, so a single error banner is sufficient).
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
      {saveError && (
        <p className="mb-2 text-xs text-danger">Save failed: {saveError}</p>
      )}
      <div className="divide-y divide-border rounded-md border border-border">
        {departments.map((dept) => (
          <DepartmentRow key={dept.name} dept={dept} />
        ))}
      </div>
    </SectionCard>
  )
}
