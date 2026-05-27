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

type BudgetPreview = NonNullable<ReturnType<typeof computeBudgetPreview>>

const REBALANCE_OPTIONS: readonly { value: RebalanceMode; label: string }[] = [
  { value: 'scale_existing', label: 'Scale down' },
  { value: 'none', label: 'Keep as-is' },
  { value: 'reject_if_over', label: 'Cancel if over' },
]

function PreviewDescription({ pack }: { pack: PackInfoResponse | null }) {
  return (
    <Dialog.Description className="mt-1 text-xs text-text-secondary">
      {pack ? `${pack.agent_count} agent(s), ${pack.department_count} department(s)` : ''}
      {pack && pack.department_count > 0 && (
        <span className="ml-1 text-warning">
          . Estimated values, final values come from API after apply
        </span>
      )}
    </Dialog.Description>
  )
}

function BudgetSnapshot({ preview, wouldExceed }: { preview: BudgetPreview; wouldExceed: boolean }) {
  return (
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
  )
}

function RebalanceModeSelect({
  mode,
  onChange,
}: {
  mode: RebalanceMode
  onChange: (mode: RebalanceMode) => void
}) {
  return (
    <div>
      <p className="mb-2 text-xs text-text-secondary">Budget would exceed 100%. Choose a strategy:</p>
      <SegmentedControl
        label="Rebalance strategy"
        value={mode}
        onChange={onChange}
        options={REBALANCE_OPTIONS}
        size="sm"
      />
    </div>
  )
}

function PreviewTable({ departments }: { departments: BudgetPreview['departments'] }) {
  return (
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
          {departments.map((d) => (
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
  )
}

interface PreviewBodyProps {
  preview: BudgetPreview
  wouldExceed: boolean
  mode: RebalanceMode
  setMode: (mode: RebalanceMode) => void
}

function PreviewBody({ preview, wouldExceed, mode, setMode }: PreviewBodyProps) {
  return (
    <div className="mt-4 space-y-4">
      <BudgetSnapshot preview={preview} wouldExceed={wouldExceed} />
      {wouldExceed && <RebalanceModeSelect mode={mode} onChange={setMode} />}
      {wouldExceed && mode === 'scale_existing' && <PreviewTable departments={preview.departments} />}
    </div>
  )
}

interface ApplyFooterProps {
  pack: PackInfoResponse | null
  wouldExceed: boolean
  mode: RebalanceMode
  applying: boolean
  onApply: (packName: string, mode: RebalanceMode) => Promise<void>
}

function ApplyFooter({ pack, wouldExceed, mode, applying, onApply }: ApplyFooterProps) {
  return (
    <div className="mt-6 flex justify-end gap-3">
      <Dialog.Close>
        <Button variant="outline" disabled={applying}>
          Cancel
        </Button>
      </Dialog.Close>
      <Button
        onClick={() => pack && onApply(pack.name, wouldExceed ? mode : 'scale_existing')}
        disabled={applying || !pack}
      >
        {applying && <Loader2 className="mr-2 size-4 animate-spin" />}
        Apply
      </Button>
    </div>
  )
}

/**
 * Rebalance-mode state that resets to the default strategy each time the
 * dialog opens for a pack or switches packs, so a prior selection never
 * leaks into a later apply (react.dev "Adjusting some state when a prop
 * changes").
 */
function useRebalanceMode(
  open: boolean,
  pack: PackInfoResponse | null,
): { mode: RebalanceMode; setMode: (mode: RebalanceMode) => void } {
  const [mode, setMode] = useState<RebalanceMode>('scale_existing')
  const packKey = open ? (pack?.name ?? '') : null
  const [prevPackKey, setPrevPackKey] = useState<string | null>(null)
  if (packKey !== prevPackKey) {
    setPrevPackKey(packKey)
    if (packKey !== null) setMode('scale_existing')
  }
  return { mode, setMode }
}

export function PackApplyPreviewDialog({
  open,
  onOpenChange,
  pack,
  currentDepartments,
  onApply,
  applying,
}: PackApplyPreviewDialogProps) {
  const { mode, setMode } = useRebalanceMode(open, pack)

  const preview = useMemo(() => {
    if (!pack) return null
    // The pack's per-department budgets aren't in PackInfoResponse, so
    // the preview uses a placeholder; the real numbers come from the API
    // response after apply.
    const packDepts =
      pack.department_count > 0
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
          <PreviewDescription pack={pack} />

          {preview && (
            <PreviewBody preview={preview} wouldExceed={wouldExceed} mode={mode} setMode={setMode} />
          )}

          <ApplyFooter
            pack={pack}
            wouldExceed={wouldExceed}
            mode={mode}
            applying={applying}
            onApply={onApply}
          />
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
