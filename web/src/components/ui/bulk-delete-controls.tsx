import { AnimatePresence } from 'motion/react'
import { Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { BulkActionBar } from '@/components/ui/bulk-action-bar'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { formatNumber } from '@/utils/format'
import type { BulkSelection } from '@/hooks/use-bulk-selection'
import type { BulkDeleteNoun } from '@/stores/_bulk-delete'

export interface BulkDeleteControlsProps {
  /** Selection state and the delete it runs, from `useBulkSelection`. */
  selection: BulkSelection
  /** What is being deleted, singular and plural. The same shape the store
   *  words its toasts from, so the bar and the toast cannot disagree. */
  noun: BulkDeleteNoun
  /** What deleting these costs, in the operator's terms. */
  description: string
  /** Distinguishes this list's toolbar from another on the same page. */
  ariaLabel: string
}

/**
 * The bar and the confirmation behind every "delete the rows I picked".
 *
 * One component rather than a copy per list: the operator meets this on
 * projects, plans and the task board, and three copies is how one of them comes
 * to skip the confirmation or word the count differently.
 */
export function BulkDeleteControls({
  selection,
  noun,
  description,
  ariaLabel,
}: BulkDeleteControlsProps) {
  const { selectedCount, deleting } = selection
  const label = selectedCount === 1 ? noun.one : noun.many
  return (
    <>
      <AnimatePresence>
        {selectedCount > 0 && (
          <BulkActionBar
            selectedCount={selectedCount}
            onClear={selection.clear}
            loading={deleting}
            ariaLabel={ariaLabel}
          >
            <Button
              size="sm"
              variant="outline"
              className="gap-1 border-danger/30 text-danger hover:bg-danger/10"
              onClick={selection.openConfirm}
              disabled={deleting}
            >
              <Trash2 className="size-3.5" />
              Delete {formatNumber(selectedCount)}
            </Button>
          </BulkActionBar>
        )}
      </AnimatePresence>

      <ConfirmDialog
        open={selection.confirmOpen}
        onOpenChange={(open) => {
          // A close while the delete is in flight is ignored rather than
          // queued: the rows are already going, and reopening on the answer
          // would put a dialog back over a list that has moved on.
          if (!open && !deleting) selection.closeConfirm()
        }}
        title={`Delete ${formatNumber(selectedCount)} ${label.toLowerCase()}?`}
        description={description}
        confirmLabel={`Delete ${formatNumber(selectedCount)}`}
        variant="destructive"
        loading={deleting}
        onConfirm={selection.runDelete}
      />
    </>
  )
}
