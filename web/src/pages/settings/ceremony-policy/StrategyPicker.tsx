import type { CeremonyStrategyType } from '@/api/types/ceremony-policy'
import { SelectField } from '@/components/ui/select-field'
import {
  CEREMONY_STRATEGY_DESCRIPTIONS,
  CEREMONY_STRATEGY_LABELS,
  CEREMONY_STRATEGY_TYPES,
} from '@/stores/ceremony-policy-constants'
import { makeEnumParser } from '@/utils/type-guards'
import { VelocityUnitIndicator } from './VelocityUnitIndicator'

const STRATEGY_OPTIONS = CEREMONY_STRATEGY_TYPES.map((s) => ({
  value: s,
  label: CEREMONY_STRATEGY_LABELS[s],
}))

const parseCeremonyStrategy = makeEnumParser<CeremonyStrategyType>(CEREMONY_STRATEGY_TYPES)

export interface StrategyPickerProps {
  value: CeremonyStrategyType
  onChange: (strategy: CeremonyStrategyType) => void
  disabled?: boolean | undefined
}

export function StrategyPicker({ value, onChange, disabled }: StrategyPickerProps) {
  return (
    <div className="space-y-2">
      <SelectField
        label="Scheduling Strategy"
        options={STRATEGY_OPTIONS}
        value={value}
        onChange={(v) => {
          const strategy = parseCeremonyStrategy(v)
          if (strategy) onChange(strategy)
        }}
        disabled={disabled}
      />
      <p className="text-xs text-text-secondary">
        {CEREMONY_STRATEGY_DESCRIPTIONS[value]}
      </p>
      <VelocityUnitIndicator strategy={value} />
    </div>
  )
}
