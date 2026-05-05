import { useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Dialog } from '@base-ui/react/dialog'
import type { Department } from '@/api/types/org'
import type { PackInfoResponse, RebalanceMode } from '@/api/types/templates'
import { Button } from '@/components/ui/button'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { computeBudgetPreview } from '@/utils/budget'

export interface PackApplyPreviewDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  pack: PackInfoResponse | null
  currentDepartments: readonly Department[]
  onApply: (packName: string, mode: RebalanceMode) => Promise<void>
  applying: boolean
}

const REBALANCE_OPTIONS: readonly { value: RebalanceMode; label: string }[] = [
  { value: 'scale_existing', label: 'Scale down' },
  { value: 'none', label: 'Keep as-is' },
  { value: 'reject_if_over', label: 'Cancel if over' },
]

export function PackApplyPreviewDialog({
  open,
  onOpenChange,
  pack,
  currentDepartments,
  onApply,
  applying,
}: PackApplyPreviewDialogProps) {
  const [mode, setMode] = useState<RebalanceMode>('scale_existing')

  const preview = useMemo(() => {
    if (!pack) return null
    // Estimate pack department budgets from the pack's department_count.
    // The actual budget percents aren't in PackInfoResponse, so we use
    // a placeholder for the preview table.  The real numbers come from
    // the API response after apply.
    const packDepts = pack.department_count > 0
      ? [{ name: `${pack.display_name} dept`, budget_percent: 8 }]
      : []
    return computeBudgetPreview(currentDepartments, packDepts)
  }, [pack, currentDepartments])

  const wouldExceed = (preview?.projectedTotal ?? 0) > 100

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-bg-base/80 backdrop-blur-sm transition-[opacity,translate] data-[closed]:opacity-0 data-[starting-style]:opacity-0" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-full max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border-bright bg-surface p-card-tight sm:p-card md:p-card-roomy shadow-[var(--so-shadow-card-hover)] transition-[opacity,translate] data-[closed]:scale-95 data-[closed]:opacity-0 data-[starting-style]:scale-95 data-[starting-style]:opacity-0">
          <Dialog.Title className="text-base font-semibold text-text-primary">
            Apply {pack?.display_name ?? 'Pack'}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-xs text-text-secondary">
            {pack
              ? `${pack.agent_count} agent(s), ${pack.department_count} department(s)`
              : ''}
            {pack && pack.department_count > 0 && (
              <span className="ml-1 text-warning">. Estimated values, final values come from API after apply</span>
            )}
          </Dialog.Description>

          {preview && (
            <div className="mt-4 space-y-4">
              {/* Budget snapshot */}
              <div className="flex gap-4 text-xs">
                <div>
                  <span className="text-text-muted">Current: </span>
                  <span className="font-mono font-medium text-text-primary">
                    {preview.currentTotal.toFixed(1)}%
                  </span>
                </div>
                <div>
                  <span className="text-text-muted">Pack adds: </span>
                  <span className="font-mono font-medium text-text-primary">
                    {preview.packTotal.toFixed(1)}%
                  </span>
                </div>
                <div>
                  <span className="text-text-muted">Projected: </span>
                  <span className={`font-mono font-medium ${wouldExceed ? 'text-danger' : 'text-success'}`}>
                    {preview.projectedTotal.toFixed(1)}%
                  </span>
                </div>
              </div>

              {/* Rebalance mode */}
              {wouldExceed && (
                <div>
                  <p className="mb-2 text-xs text-text-secondary">
                    Budget would exceed 100%. Choose a strategy:
                  </p>
                  <SegmentedControl
                    label="Rebalance strategy"
                    value={mode}
                    onChange={setMode}
                    options={REBALANCE_OPTIONS}
                    size="sm"
                  />
                </div>
              )}

              {/* Preview table */}
              {wouldExceed && mode === 'scale_existing' && (
                <div className="overflow-x-auto rounded border border-border">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border bg-bg-surface">
                        <th className="px-3 py-1.5 text-left font-medium text-text-muted">Department</th>
                        <th className="px-3 py-1.5 text-right font-medium text-text-muted">Current %</th>
                        <th className="px-3 py-1.5 text-right font-medium text-text-muted">After %</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {preview.departments.map((d) => (
                        <tr key={d.name}>
                          <td className="px-3 py-1.5 text-text-primary">{d.name}</td>
                          <td className="px-3 py-1.5 text-right font-mono text-text-secondary">
                            {d.before.toFixed(1)}
                          </td>
                          <td className="px-3 py-1.5 text-right font-mono text-text-primary">
                            {d.after.toFixed(1)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          <div className="mt-6 flex justify-end gap-3">
            <Dialog.Close>
              <Button variant="outline" disabled={applying}>Cancel</Button>
            </Dialog.Close>
            <Button
              onClick={() => pack && onApply(pack.name, wouldExceed ? mode : 'scale_existing')}
              disabled={applying || !pack}
            >
              {applying && <Loader2 className="mr-2 size-4 animate-spin" />}
              Apply
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
