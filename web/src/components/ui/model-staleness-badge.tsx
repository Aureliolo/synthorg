import { AlertCircle } from 'lucide-react'
import type { ModelStaleness } from '@/api/types'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { cn } from '@/lib/utils'
import { formatDateTime } from '@/utils/format'

const REASON_LABEL: Record<ModelStaleness['reason'], string> = {
  removed_from_catalog: 'Removed',
  deprecated: 'Deprecated',
}

export interface ModelStalenessBadgeProps {
  /** The model's staleness marker, or null/undefined when current. */
  stale: ModelStaleness | null | undefined
  className?: string | undefined
}

/**
 * Warning badge shown next to a model that the periodic refresh service
 * flagged stale (removed from the provider's live catalogue or marked
 * deprecated). Renders nothing for a current model. A hover/focus
 * tooltip explains when it was flagged and the suggested replacement.
 */
export function ModelStalenessBadge({ stale, className }: ModelStalenessBadgeProps) {
  if (stale == null) return null
  const successor = stale.successor_model_id
  return (
    <InfoTooltip
      content={
        <div className="flex flex-col gap-1">
          <span className="font-semibold">
            {REASON_LABEL[stale.reason]} from the provider catalogue
          </span>
          <span>Flagged {formatDateTime(stale.flagged_at)}.</span>
          {successor != null && <span>Suggested replacement: {successor}.</span>}
        </div>
      }
    >
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded-sm border border-warning/30',
          'bg-warning/10 px-1.5 py-0.5 text-compact font-medium text-warning',
          className,
        )}
      >
        <AlertCircle className="size-3" aria-hidden="true" />
        {REASON_LABEL[stale.reason]}
      </span>
    </InfoTooltip>
  )
}
