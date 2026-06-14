import { AlertTriangle } from 'lucide-react'
import type { CeremonyStrategyType } from '@/api/types/ceremony-policy'
import { CEREMONY_STRATEGY_LABELS } from '@/stores/ceremony-policy-constants'

export interface StrategyChangeWarningProps {
  currentStrategy: CeremonyStrategyType
  activeStrategy: CeremonyStrategyType
}

export function StrategyChangeWarning({
  currentStrategy,
  activeStrategy,
}: StrategyChangeWarningProps) {
  if (currentStrategy === activeStrategy) return null

  return (
    <div role="status" aria-live="polite" className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/5 p-card">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />
      <div className="space-y-1">
        <p className="text-sm font-medium text-foreground">
          Strategy change pending
        </p>
        <p className="text-xs text-text-secondary">
          Changing from <span className="font-medium">{CEREMONY_STRATEGY_LABELS[activeStrategy]}</span> to{' '}
          <span className="font-medium">{CEREMONY_STRATEGY_LABELS[currentStrategy]}</span> will
          take effect at the next sprint start. The velocity rolling-average window will reset.
        </p>
      </div>
    </div>
  )
}
