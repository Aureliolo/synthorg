import type { ResolvedCeremonyPolicyResponse, VelocityCalcType } from '@/api/types/ceremony-policy'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { ToggleField } from '@/components/ui/toggle-field'
import { PolicySourceBadge } from '@/components/ui/policy-source-badge'
import { VELOCITY_CALC_LABELS, VELOCITY_CALC_TYPES } from '@/stores/ceremony-policy-constants'

const VELOCITY_OPTIONS = VELOCITY_CALC_TYPES.map((v) => ({
  value: v,
  label: VELOCITY_CALC_LABELS[v],
}))

export interface PolicyFieldsPanelProps {
  velocityCalculator: VelocityCalcType
  autoTransition: boolean
  transitionThreshold: number
  onVelocityCalculatorChange: (calc: VelocityCalcType) => void
  onAutoTransitionChange: (value: boolean) => void
  onTransitionThresholdChange: (value: number) => void
  resolvedPolicy?: ResolvedCeremonyPolicyResponse | null | undefined
  disabled?: boolean | undefined
}

export function PolicyFieldsPanel({
  velocityCalculator,
  autoTransition,
  transitionThreshold,
  onVelocityCalculatorChange,
  onAutoTransitionChange,
  onTransitionThresholdChange,
  resolvedPolicy,
  disabled,
}: PolicyFieldsPanelProps) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <div className="flex-1">
          <SelectField
            label="Velocity Calculator"
            options={VELOCITY_OPTIONS}
            value={velocityCalculator}
            onChange={(v) => onVelocityCalculatorChange(v as VelocityCalcType)}
            disabled={disabled}
          />
        </div>
        {resolvedPolicy && (
          <PolicySourceBadge source={resolvedPolicy.velocity_calculator.source} className="mt-5" />
        )}
      </div>

      <div className="flex items-center gap-2">
        <div className="flex-1">
          <ToggleField
            label="Auto-transition"
            description="Automatically transition sprints when strategy conditions are met"
            checked={autoTransition}
            onChange={onAutoTransitionChange}
            disabled={disabled}
          />
        </div>
        {resolvedPolicy && (
          <PolicySourceBadge source={resolvedPolicy.auto_transition.source} />
        )}
      </div>

      {autoTransition && (
        <div className="flex items-center gap-2">
          <div className="flex-1">
            <InputField
              label="Transition Threshold"
              type="number"
              value={String(transitionThreshold)}
              onChange={(e) => {
                const val = Number(e.target.value)
                if (Number.isFinite(val)) {
                  onTransitionThresholdChange(Math.min(1.0, Math.max(0.01, val)))
                }
              }}
              disabled={disabled}
              hint="Fraction of completion required for auto-transition (0.01 to 1.0)"
            />
          </div>
          {resolvedPolicy && (
            <PolicySourceBadge source={resolvedPolicy.transition_threshold.source} className="mt-5" />
          )}
        </div>
      )}
    </div>
  )
}
