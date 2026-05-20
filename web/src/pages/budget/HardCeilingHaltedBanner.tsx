import { useState } from 'react'
import { AlertOctagon, RotateCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/utils/format'

export interface HardCeilingHaltedBannerProps {
  /** Total cost accumulated when the run halted. */
  accumulatedCost: number
  /** Ceiling value that was crossed. */
  ceilingAmount: number
  /** ISO 4217 currency code stamped on both values. */
  currency: string
  /** Forecast id linking back to the original pre-flight estimate. */
  forecastId: string | null
  /** True while a raise-ceiling mutation is in flight. */
  mutating?: boolean
  /** Invoked with the new ceiling when the operator confirms a raise. */
  onRaiseCeiling: (newCeiling: number) => void
}

/**
 * Banner shown when a run halts on a hard-ceiling crossing. The
 * operator sets a new ceiling (defaulted to 1.5x the accumulated
 * cost so resume is meaningful) and the engine resumes the parked
 * context.
 */
export function HardCeilingHaltedBanner({
  accumulatedCost,
  ceilingAmount,
  currency,
  forecastId,
  mutating = false,
  onRaiseCeiling,
}: HardCeilingHaltedBannerProps) {
  const suggested = Math.round(accumulatedCost * 1.5 * 100) / 100
  const [newCeiling, setNewCeiling] = useState<string>(String(suggested))
  // Re-suggest when a different parked run is shown in the same mounted
  // banner (set-state-during-render); otherwise the input keeps the
  // first run's default ceiling.
  const [trackedCost, setTrackedCost] = useState<number>(accumulatedCost)
  if (accumulatedCost !== trackedCost) {
    setTrackedCost(accumulatedCost)
    setNewCeiling(String(suggested))
  }

  const parsed = Number.parseFloat(newCeiling)
  const valid = Number.isFinite(parsed) && parsed > accumulatedCost

  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col gap-section-gap rounded-lg border p-card',
        'border-danger/30 bg-danger/5 text-foreground',
      )}
    >
      <div className="flex items-start gap-3">
        <AlertOctagon
          aria-hidden="true"
          className="mt-0.5 size-5 shrink-0 text-danger"
        />
        <div className="flex flex-col gap-1">
          <span className="text-sm font-semibold text-danger">
            Run halted: hard ceiling exceeded
          </span>
          <span className="text-xs text-muted-foreground">
            Accumulated{' '}
            <span className="font-mono">
              {formatCurrency(accumulatedCost, currency)}
            </span>{' '}
            crossed the{' '}
            <span className="font-mono">
              {formatCurrency(ceilingAmount, currency)}
            </span>{' '}
            ceiling. Raise the ceiling to resume.
            {forecastId !== null ? (
              <>
                {' '}
                <span className="text-text-muted">
                  (forecast {forecastId.slice(0, 8)}…)
                </span>
              </>
            ) : null}
          </span>
        </div>
      </div>

      <label
        htmlFor="raise-ceiling-input"
        className="flex flex-col gap-1 text-sm"
      >
        <span className="font-medium text-foreground">New hard ceiling</span>
        <span className="text-xs text-muted-foreground">
          Must be strictly greater than the accumulated cost
          ({formatCurrency(accumulatedCost, currency)}). Default is 1.5x.
        </span>
        <input
          id="raise-ceiling-input"
          type="number"
          min={0}
          step={0.01}
          value={newCeiling}
          onChange={(event) => setNewCeiling(event.target.value)}
          className="mt-1 rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground"
          aria-invalid={!valid}
        />
      </label>

      <div className="flex justify-end">
        <Button
          onClick={() => {
            if (valid) onRaiseCeiling(parsed)
          }}
          disabled={!valid || mutating}
        >
          <RotateCw aria-hidden="true" className="mr-2 size-4" />
          Raise ceiling and resume
        </Button>
      </div>
    </div>
  )
}
