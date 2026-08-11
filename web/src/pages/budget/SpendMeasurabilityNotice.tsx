import { AlertTriangle } from 'lucide-react'

import type { SpendMeasurability } from '@/api/types/budget'
import { cn } from '@/lib/utils'

export interface SpendMeasurabilityNoticeProps {
  /** Verdict for the shown period; `undefined` while the period loads. */
  measurability: SpendMeasurability | undefined
}

const MESSAGE: Record<Exclude<SpendMeasurability, 'measured'>, string> = {
  unmeasurable:
    'Spend is not measurable here. Every provider serving this period bills by ' +
    'flat subscription, so the totals below are a correct zero that measures ' +
    'nothing, and the money ceiling cannot bind. Set a token ceiling instead.',
  mixed:
    'Spend is only partly measurable. Some providers serving this period bill by ' +
    'flat subscription, so the totals below are correct for what they cover and ' +
    'understate the rest.',
}

/**
 * Says when the money figures cannot measure what was spent.
 *
 * A provider that bills by flat subscription records a cost of 0.0 on every
 * call. That is the right number and it is not headroom, so an operator
 * reading 0.00 has to be told which of the two zeroes this is.
 */
export function SpendMeasurabilityNotice({ measurability }: SpendMeasurabilityNoticeProps) {
  if (measurability === undefined || measurability === 'measured') return null

  return (
    <div
      className={cn(
        'flex items-start gap-2 rounded-lg border p-card text-sm',
        measurability === 'unmeasurable'
          ? 'border-warning/30 bg-warning/5 text-warning'
          : 'border-border bg-surface text-text-secondary',
      )}
      role="status"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <span>{MESSAGE[measurability]}</span>
    </div>
  )
}
