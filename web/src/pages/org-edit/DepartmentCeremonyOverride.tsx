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
} from '@/stores/ceremony-policy-constants'

const STRATEGY_OPTIONS = CEREMONY_STRATEGY_TYPES.map((s) => ({
  value: s,
  label: CEREMONY_STRATEGY_LABELS[s],
}))

const VELOCITY_OPTIONS = VELOCITY_CALC_TYPES.map((v) => ({
  value: v,
  label: VELOCITY_CALC_LABELS[v],
}))

const THRESHOLD_MIN = 0.01
const THRESHOLD_MAX = 1.0

interface AutoTransitionRowProps {
  policy: CeremonyPolicyConfig | null | undefined
  onChange: (policy: CeremonyPolicyConfig | null) => void
  disabled?: boolean | undefined
}

function AutoTransitionRow({ policy, onChange, disabled }: AutoTransitionRowProps) {
  const autoTransition = policy?.auto_transition ?? true
  const threshold = policy?.transition_threshold ?? THRESHOLD_MAX

  // Hold the raw input text locally so the user can type partial values
  // like "0." or clear the field; the numeric value is clamped and
  // committed on blur, not on every keystroke (which would reset the
  // field mid-edit). Re-sync the draft when the committed threshold
  // changes from outside (react.dev "Adjusting some state when a prop
  // changes").
  const [thresholdText, setThresholdText] = useState(String(threshold))
  const [prevThreshold, setPrevThreshold] = useState(threshold)
  if (threshold !== prevThreshold) {
    setPrevThreshold(threshold)
    setThresholdText(String(threshold))
  }

  const commitThreshold = useCallback(() => {
    const val = Number(thresholdText)
    if (!Number.isFinite(val)) {
      setThresholdText(String(threshold))
      return
    }
    const clamped = Math.min(THRESHOLD_MAX, Math.max(THRESHOLD_MIN, val))
    setThresholdText(String(clamped))
    if (clamped !== threshold) onChange({ ...policy, transition_threshold: clamped })
  }, [thresholdText, threshold, policy, onChange])

  return (
    <>
      <ToggleField
        label="Auto-transition"
        checked={autoTransition}
        onChange={(v) => onChange({ ...policy, auto_transition: v })}
        disabled={disabled}
      />
      {autoTransition && (
        <InputField
          label="Transition Threshold"
          type="number"
          value={thresholdText}
          onChange={(e) => setThresholdText(e.target.value)}
          onBlur={commitThreshold}
          disabled={disabled}
          hint="0.01 to 1.0"
        />
      )}
    </>
  )
}

interface CeremonyPolicyFieldsProps {
  policy: CeremonyPolicyConfig | null | undefined
  onChange: (policy: CeremonyPolicyConfig | null) => void
  onStrategyChange: (s: CeremonyStrategyType) => void
  disabled?: boolean | undefined
}

function CeremonyPolicyFields({ policy, onChange, onStrategyChange, disabled }: CeremonyPolicyFieldsProps) {
  const strategy = policy?.strategy ?? 'task_driven'
  const velocityCalc = policy?.velocity_calculator ?? STRATEGY_DEFAULT_VELOCITY_CALC[strategy]
  return (
    <div className="space-y-3 pl-2 border-l-2 border-accent/20">
      <SelectField
        label="Strategy"
        options={STRATEGY_OPTIONS}
        value={strategy}
        onChange={(v) => onStrategyChange(v as CeremonyStrategyType)}
        disabled={disabled}
      />
      <SelectField
        label="Velocity Calculator"
        options={VELOCITY_OPTIONS}
        value={velocityCalc}
        onChange={(v) => onChange({ ...policy, velocity_calculator: v as VelocityCalcType })}
        disabled={disabled}
      />
      <AutoTransitionRow policy={policy} onChange={onChange} disabled={disabled} />
    </div>
  )
}

export interface DepartmentCeremonyOverrideProps {
  policy: CeremonyPolicyConfig | null | undefined
  onChange: (policy: CeremonyPolicyConfig | null) => void
  disabled?: boolean | undefined
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
        // Preserve existing policy fields if available, otherwise start empty.
        onChange(policy ?? {})
        setExpanded(true)
      }
    },
    [onChange, policy],
  )

  const handleStrategyChange = useCallback(
    (s: CeremonyStrategyType) => {
      // Only reset config/velocity when strategy actually changes.
      if (s === policy?.strategy) return
      // Clear strategy_config because different strategies have different
      // config schemas, and this component has no StrategyConfigPanel.
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
          <InheritToggle inherit={!hasOverride} onChange={handleInheritChange} disabled={disabled} />
          {hasOverride && (
            <CeremonyPolicyFields
              policy={policy}
              onChange={onChange}
              onStrategyChange={handleStrategyChange}
              disabled={disabled}
            />
          )}
        </div>
      )}
    </div>
  )
}
