import { useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogCloseButton,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { formatCurrency } from '@/utils/format'
import type { Forecast } from '@/api/types'

export interface BudgetForecastDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  forecast: Forecast | null
  loading?: boolean
  mutating?: boolean
  onApprove: (ceilingAmount: number | null) => void
  onReject: () => void
}

export function BudgetForecastDialog({
  open,
  onOpenChange,
  forecast,
  loading = false,
  mutating = false,
  onApprove,
  onReject,
}: BudgetForecastDialogProps) {
  const suggested = forecast ? Math.round(forecast.upper_bound * 1.5 * 100) / 100 : 0
  const [ceilingInput, setCeilingInput] = useState<string>(String(suggested))

  const handleApprove = () => {
    const parsed = Number.parseFloat(ceilingInput)
    onApprove(Number.isFinite(parsed) && parsed > 0 ? parsed : null)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <div className="flex flex-col gap-1">
            <DialogTitle>Pre-flight cost forecast</DialogTitle>
            <DialogDescription>
              Approve before this brief commits real spend.
            </DialogDescription>
          </div>
          <DialogCloseButton />
        </DialogHeader>

        <div className="flex flex-col gap-section-gap p-card">
          {loading || !forecast ? (
            <div className="h-24 animate-pulse rounded-lg border border-border bg-card" />
          ) : (
            <>
              <div className="flex flex-col items-center gap-1">
                <span className="text-xs uppercase tracking-wide text-text-muted">
                  Estimated cost
                </span>
                <span className="font-mono text-2xl font-semibold text-foreground">
                  {formatCurrency(forecast.estimated_cost, forecast.currency)}
                </span>
                <span className="text-xs text-muted-foreground">
                  range {formatCurrency(forecast.lower_bound, forecast.currency)}
                  {' – '}
                  {formatCurrency(forecast.upper_bound, forecast.currency)}
                </span>
              </div>

              <label
                htmlFor="ceiling-input"
                className="flex flex-col gap-1 text-sm"
              >
                <span className="font-medium text-foreground">
                  Hard ceiling
                </span>
                <span className="text-xs text-muted-foreground">
                  Halts the org cleanly if cost crosses this line. Default is
                  1.5x the forecast upper bound; tighten or widen as needed.
                </span>
                <input
                  id="ceiling-input"
                  type="number"
                  min={0}
                  step={0.01}
                  value={ceilingInput}
                  onChange={(event) => setCeilingInput(event.target.value)}
                  className="mt-1 rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground"
                />
              </label>

              <div className="flex justify-end gap-2 border-t border-border pt-card-tight">
                <Button
                  variant="ghost"
                  onClick={onReject}
                  disabled={mutating}
                >
                  Reject
                </Button>
                <Button onClick={handleApprove} disabled={mutating}>
                  Approve
                </Button>
              </div>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
