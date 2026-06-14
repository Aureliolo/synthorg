import type { CeremonyStrategyType, VelocityCalcType } from '@/api/types/ceremony-policy'
import { STRATEGY_DEFAULT_VELOCITY_CALC, VELOCITY_UNIT_LABELS } from '@/stores/ceremony-policy-constants'

export interface VelocityUnitIndicatorProps {
  strategy: CeremonyStrategyType
  velocityCalculator?: VelocityCalcType | null
}

export function VelocityUnitIndicator({
  strategy,
  velocityCalculator,
}: VelocityUnitIndicatorProps) {
  const effectiveCalc = velocityCalculator ?? STRATEGY_DEFAULT_VELOCITY_CALC[strategy]
  const unit = VELOCITY_UNIT_LABELS[effectiveCalc]

  return (
    <span className="text-xs text-text-muted">
      Velocity unit: <span className="font-medium text-text-secondary">{unit}</span>
    </span>
  )
}
