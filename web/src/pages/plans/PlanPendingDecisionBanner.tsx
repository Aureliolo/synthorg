import { Link } from 'react-router'

import type { Plan } from '@/api/types/plans'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ROUTES } from '@/router/routes'

interface PlanPendingDecisionBannerProps {
  plan: Plan
}

/**
 * Say that this initiative has stopped and is waiting on the reader.
 *
 * The status badge answers "what did the org last do with this plan", which is
 * a different question. An initiative that ran out of automatic recovery keeps
 * whatever status it had, so the page reads `executing` while nothing executes;
 * the decision waiting in the approvals queue is the fact that says otherwise,
 * and it arrives resolved on the row rather than being looked up here.
 */
export function PlanPendingDecisionBanner({ plan }: PlanPendingDecisionBannerProps) {
  const decision = plan.pending_decision
  if (decision === null) return null
  return (
    <ErrorBanner
      severity="warning"
      title={decision.title}
      // The title and the reason are prose whoever raised this chose, and the
      // approvals queue accepts an item from anything holding write access, so
      // the requester is shown beside them rather than left for the drawer.
      description={`${decision.reason} Raised by ${decision.requested_by}.`}
      action={
        <Button variant="outline" size="sm" asChild>
          {/* Labelled by the decision, navigated by its id. */}
          <Link to={`${ROUTES.APPROVALS}?selected=${encodeURIComponent(decision.approval_id)}`}>
            Answer it
          </Link>
        </Button>
      }
    />
  )
}
