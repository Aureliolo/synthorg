import type { ReactNode } from 'react'
import { motion } from 'motion/react'
import { Minus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { springDefault, tweenExitFast } from '@/lib/motion'
import { formatNumber } from '@/utils/format'

export interface BulkActionBarProps {
  /** Currently selected item count. Rendered on the left (`N selected`). */
  selectedCount: number
  /** Invoked when the user clicks the Clear button -- caller should empty selection. */
  onClear: () => void
  /** Action buttons (e.g. a destructive `Delete N` button). Caller owns the rendering. */
  children: ReactNode
  /**
   * Disable every interactive control while a batch op is in flight. The
   * bar does not manage loading state internally so callers can show
   * per-action spinners alongside the disabled state.
   */
  loading?: boolean | undefined
  /** Optional aria-label override. Defaults to "Bulk actions". */
  ariaLabel?: string | undefined
}

const BAR_VARIANTS = {
  initial: { y: '100%', opacity: 0 },
  animate: { y: 0, opacity: 1, transition: springDefault },
  exit: { y: '100%', opacity: 0, transition: tweenExitFast },
}

/**
 * Sticky bottom bar shown while the user has multi-selected rows in a
 * list view. Mirrors the approvals BatchActionBar layout so the dashboard
 * has a single idiom for bulk operations (Workflows, Projects, and any
 * future list page wire into the same primitive).
 */
export function BulkActionBar({
  selectedCount,
  onClear,
  children,
  loading,
  ariaLabel = 'Bulk actions',
}: BulkActionBarProps) {
  return (
    <motion.div
      className="fixed inset-x-0 bottom-0 z-30 flex items-center justify-center px-4 pb-4"
      variants={BAR_VARIANTS}
      initial="initial"
      animate="animate"
      exit="exit"
    >
      <div
        className="flex items-center gap-3 rounded-lg border border-border bg-surface p-card shadow-[var(--so-shadow-card-hover)]"
        role="toolbar"
        aria-label={ariaLabel}
      >
        <span className="text-sm font-medium text-foreground" aria-live="polite">
          {formatNumber(selectedCount)} selected
        </span>

        <div className="h-4 w-px bg-border" aria-hidden="true" />

        {children}

        <div className="h-4 w-px bg-border" aria-hidden="true" />

        <Button
          size="sm"
          variant="ghost"
          className="gap-1 text-muted-foreground"
          onClick={onClear}
          disabled={loading}
        >
          <Minus className="size-3.5" />
          Clear
        </Button>
      </div>
    </motion.div>
  )
}
