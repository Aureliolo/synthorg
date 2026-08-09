import { History } from 'lucide-react'

import type { LifecycleTransition } from '@/api/types/plans'
import { SectionCard } from '@/components/ui/section-card'
import { Skeleton } from '@/components/ui/skeleton'
import { usePlanTransitions } from '@/hooks/usePlanTransitions'
import { formatDateTime } from '@/utils/format'

function TransitionRow({ transition }: { transition: LifecycleTransition }) {
  const from = transition.from_status ?? 'opened'
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs">
      <span className="font-mono text-text-secondary">
        {from} &rarr; {transition.to_status}
      </span>
      <span className="text-muted-foreground">
        {formatDateTime(transition.occurred_at)}
      </span>
      <span className="text-muted-foreground">
        {/* Null means nothing asked: a reconciler pass or a rollup moved it on
            its own schedule, which is itself the answer to "who". */}
        {transition.requested_by ?? 'the system'}
      </span>
      {transition.reason !== null && (
        <span className="basis-full text-text-secondary">{transition.reason}</span>
      )}
    </li>
  )
}

/**
 * How this plan reached its current status, from the durable ledger.
 *
 * The status badge says where the plan is now. Before this, the answer to "who
 * moved it, and when" lived only in a container's log, so a plan that reached
 * COMPLETED had no record an operator could query: the claim that only the
 * evaluate stage writes that status was unprovable from the product itself.
 */
export function PlanHistoryPanel({ planId }: { planId: string | undefined }) {
  const { transitions, loading, error } = usePlanTransitions(planId)
  if (planId === undefined) return null
  return (
    <SectionCard title="Status history" icon={History}>
      {loading && transitions.length === 0 ? (
        <Skeleton className="h-16 w-full" />
      ) : error !== null && transitions.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          Status history unavailable: {error}
        </p>
      ) : transitions.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No transitions recorded for this plan yet.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {transitions.map((transition) => (
            <TransitionRow key={transition.id} transition={transition} />
          ))}
        </ul>
      )}
    </SectionCard>
  )
}
