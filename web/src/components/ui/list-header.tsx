import { RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { formatNumber } from '@/utils/format'

export interface ListHeaderProps {
  title: string
  /** Total item count shown in subtle muted text next to the title (e.g. "Tasks (42)"). */
  count?: number
  /** Override the count label when the default parenthesised format isn't right. */
  countLabel?: string
  description?: string
  /** Primary action slot (typically a single `<Button>`), rendered top-right. */
  primaryAction?: ReactNode
  /** Secondary slot for search/filter/sort controls rendered below the title row on narrow viewports, inline on wide ones. */
  secondaryActions?: ReactNode
  /**
   * Quiet in-progress indicator surfaced inline with the title. Use
   * to signal "background refresh of already-rendered data" (e.g. a
   * scheduled poll tick) without competing with the page's full-page
   * loading skeleton. The icon is decorative; an sr-only label gives
   * AT users the "Refreshing" cue.
   */
  refreshing?: boolean
  className?: string
}

/**
 * Standardised header for list / index pages.
 *
 * Layout: title + count on the left, primary action on the right, optional
 * secondary controls wrap below on narrow viewports. Keeps the primary action
 * placement consistent across the dashboard so operators don't have to hunt
 * for "New X" between pages.
 */
function _resolveCountText(count: number | undefined, countLabel: string | undefined): string | undefined {
  if (countLabel) return countLabel
  if (count !== undefined) return `(${formatNumber(count)})`
  return undefined
}

function ListHeaderTitle({
  title,
  countText,
  refreshing,
  description,
}: {
  title: string
  countText: string | undefined
  refreshing: boolean
  description: string | undefined
}) {
  return (
    <div className="min-w-0 flex-1">
      <div className="flex items-baseline gap-2">
        <h1 className="truncate text-lg font-semibold text-foreground">{title}</h1>
        {countText && (
          <span className="shrink-0 font-mono text-sm text-muted-foreground">
            {countText}
          </span>
        )}
        {refreshing && (
          <span aria-live="polite" className="shrink-0 text-muted-foreground">
            <RefreshCw className="size-3 animate-spin" aria-hidden="true" />
            <span className="sr-only">{`Refreshing ${title}`}</span>
          </span>
        )}
      </div>
      {description && (
        <p className="mt-1 text-xs text-muted-foreground">{description}</p>
      )}
    </div>
  )
}

export function ListHeader({
  title,
  count,
  countLabel,
  description,
  primaryAction,
  secondaryActions,
  refreshing = false,
  className,
}: ListHeaderProps) {
  const countText = _resolveCountText(count, countLabel)
  const hasPrimary = primaryAction != null && primaryAction !== false
  const hasSecondary = secondaryActions != null && secondaryActions !== false
  return (
    <header className={cn('flex flex-col gap-3', className)}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <ListHeaderTitle
          title={title}
          countText={countText}
          refreshing={refreshing}
          description={description}
        />
        {hasPrimary && <div className="shrink-0">{primaryAction}</div>}
      </div>
      {hasSecondary && (
        <div className="flex flex-wrap items-center gap-2">{secondaryActions}</div>
      )}
    </header>
  )
}
