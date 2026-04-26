import { useCallback, useState } from 'react'
import type { CeremonyPolicyConfig, CeremonyStrategyType, VelocityCalcType } from '@/api/types/ceremony-policy'
import { InheritToggle } from '@/components/ui/inherit-toggle'
import { SelectField } from '@/components/ui/select-field'
import { ToggleField } from '@/components/ui/toggle-field'
import { InputField } from '@/components/ui/input-field'
import {
  CEREMONY_STRATEGY_LABELS,
  CEREMONY_STRATEGY_TYPES,
  STRATEGY_DEFAULT_VELOCITY_CALC,
  VELOCITY_CALC_LABELS,
  VELOCITY_CALC_TYPES,
} from '@/utils/constants'

const STRATEGY_OPTIONS = CEREMONY_STRATEGY_TYPES.map((s) => ({
  value: s,
  label: CEREMONY_STRATEGY_LABELS[s],
}))

const VELOCITY_OPTIONS = VELOCITY_CALC_TYPES.map((v) => ({
  value: v,
  label: VELOCITY_CALC_LABELS[v],
}))

export interface DepartmentCeremonyOverrideProps {
  policy: CeremonyPolicyConfig | null | undefined
  onChange: (policy: CeremonyPolicyConfig | null) => void
  disabled?: boolean
}

export function DepartmentCeremonyOverride({
  policy,
  onChange,
  disabled,
}: DepartmentCeremonyOverrideProps) {
  // Whether the department has a non-null policy object (even if empty;
  // an empty object means "override with defaults").
  const hasOverride = policy != null
  const [expanded, setExpanded] = useState(hasOverride)

  const handleInheritChange = useCallback(
    (inherit: boolean) => {
      if (inherit) {
        onChange(null)
        setExpanded(false)
      } else {
        // Preserve existing policy fields if available, otherwise start empty
        onChange(policy ?? {})
        setExpanded(true)
      }
    },
    [onChange, policy],
  )

  const handleStrategyChange = useCallback(
    (s: CeremonyStrategyType) => {
      // Only reset config/velocity when strategy actually changes
      if (s === policy?.strategy) {
        return
      }
      // Clear strategy_config because different strategies have different
      // config schemas, and this component has no StrategyConfigPanel to
      // edit it.
      // eslint-disable-next-line @typescript-eslint/no-unused-vars -- destructure to omit strategy_config
      const { strategy_config: _omitted, ...rest } = policy ?? {}
      onChange({
        ...rest,
        strategy: s,
        velocity_calculator: STRATEGY_DEFAULT_VELOCITY_CALC[s],
      })
    },
    [policy, onChange],
  )

  return (
    <div className="border-t border-border pt-4 space-y-3">
      <button
        type="button"
        aria-expanded={expanded}
        aria-label={expanded ? 'Collapse ceremony policy' : 'Expand ceremony policy'}
        onClick={() => setExpanded(!expanded)}
        className="text-xs font-semibold uppercase tracking-wider text-text-muted hover:text-foreground"
      >
        Ceremony Policy {expanded ? '-' : '+'}
      </button>

      {expanded && (
        <div className="space-y-3">
          <InheritToggle
            inherit={!hasOverride}
            onChange={handleInheritChange}
            disabled={disabled}
          />

          {hasOverride && (
            <div className="space-y-3 pl-2 border-l-2 border-accent/20">
              <SelectField
                label="Strategy"
                options={STRATEGY_OPTIONS}
                value={policy?.strategy ?? 'task_driven'}
                onChange={(v) => handleStrategyChange(v as CeremonyStrategyType)}
                disabled={disabled}
              />

              <SelectField
                label="Velocity Calculator"
                options={VELOCITY_OPTIONS}
                value={policy?.velocity_calculator ?? STRATEGY_DEFAULT_VELOCITY_CALC[policy?.strategy ?? 'task_driven']}
                onChange={(v) => onChange({ ...policy, velocity_calculator: v as VelocityCalcType })}
                disabled={disabled}
              />

              <ToggleField
                label="Auto-transition"
                checked={policy?.auto_transition ?? true}
                onChange={(v) => onChange({ ...policy, auto_transition: v })}
                disabled={disabled}
              />

              {(policy?.auto_transition ?? true) && (
                <InputField
                  label="Transition Threshold"
                  type="number"
                  value={String(policy?.transition_threshold ?? 1.0)}
                  onChange={(e) => {
                    const val = Number(e.target.value)
                    if (!Number.isFinite(val)) return
                    onChange({ ...policy, transition_threshold: Math.min(1.0, Math.max(0.01, val)) })
                  }}
                  disabled={disabled}
                  hint="0.01 to 1.0"
                />
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
