import { AlertCircle } from 'lucide-react'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { cn } from '@/lib/utils'

export interface ToolCallingUnavailableBadgeProps {
  /**
   * The model's runtime tool-calling verdict. ``false`` means repeated
   * runtime tool-call failures proved the model cannot call tools; ``true``
   * (proven) and ``null`` / ``undefined`` (unobserved) render nothing.
   */
  toolCallsVerified: boolean | null | undefined
  className?: string | undefined
}

/**
 * Warning badge shown next to a model the runtime feedback loop downgraded
 * after repeated tool-call failures, so operators can see why the matcher no
 * longer assigns it to tool-requiring agents. Renders nothing unless the
 * verdict is an explicit ``false``. A hover/focus tooltip explains the cause
 * and the manual re-enable escape hatch.
 */
export function ToolCallingUnavailableBadge({
  toolCallsVerified,
  className,
}: ToolCallingUnavailableBadgeProps) {
  if (toolCallsVerified !== false) return null
  return (
    <InfoTooltip
      content={
        <div className="flex flex-col gap-1">
          <span className="font-semibold">Tool calling unavailable</span>
          <span>
            Repeated tool-call failures at runtime proved this model cannot
            call tools, so it is no longer assigned to tool-requiring agents.
          </span>
          <span>Re-enable it once the underlying cause is fixed.</span>
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
        No tool calling
      </span>
    </InfoTooltip>
  )
}
