import { Link } from 'react-router'
import { ChevronRight, ClipboardCheck } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { ROUTES } from '@/router/routes'

export interface PendingApprovalsCardProps {
  count: number
}

/**
 * Dashboard panel surfacing how many approvals await the operator's
 * decision, linking through to the approvals queue. Presentational: the
 * count is supplied by the caller (from ``usePendingApprovalsCount``).
 */
export function PendingApprovalsCard({ count }: PendingApprovalsCardProps) {
  return (
    <SectionCard title="Pending Approvals" icon={ClipboardCheck}>
      {count === 0 ? (
        <EmptyState
          icon={ClipboardCheck}
          title="No approvals waiting"
          description="Plans and actions needing your decision will appear here."
        />
      ) : (
        <Link
          to={ROUTES.APPROVALS}
          className="-m-2 flex items-center justify-between rounded-md p-2 transition-colors hover:bg-card-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          aria-label={`${count} ${count === 1 ? 'approval awaits' : 'approvals await'} your decision; review approvals`}
        >
          <div>
            <div className="font-mono text-metric font-bold leading-tight text-foreground">
              {count}
            </div>
            <div className="text-sm text-muted-foreground">
              {count === 1 ? 'item awaits' : 'items await'} your decision
            </div>
          </div>
          <ChevronRight className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
        </Link>
      )}
    </SectionCard>
  )
}
