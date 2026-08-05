import { Gavel, ScrollText } from 'lucide-react'

import type {
  CriterionOutcome,
  CriterionVerdict,
  PlanEvaluationAttempt,
} from '@/api/types/plans'
import { SectionCard } from '@/components/ui/section-card'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusPill } from '@/components/ui/status-pill'
import type { StatusPillTone } from '@/components/ui/status-pill'
import { usePlanEvaluation } from '@/hooks/usePlanEvaluation'
import { formatDateTime } from '@/utils/format'

const OUTCOME_LABEL: Record<CriterionOutcome, string> = {
  met: 'Met',
  partial: 'Partial',
  unmet: 'Unmet',
}

// Partial is not a pass: the objective is met or it is not, so partial reads
// the same as unmet here rather than looking like a near-miss that shipped.
const OUTCOME_TONE: Record<CriterionOutcome, StatusPillTone> = {
  met: 'success',
  partial: 'warning',
  unmet: 'danger',
}

function OutcomePill({ outcome }: { outcome: CriterionOutcome }) {
  return <StatusPill tone={OUTCOME_TONE[outcome]}>{OUTCOME_LABEL[outcome]}</StatusPill>
}

function VerdictRow({ verdict }: { verdict: CriterionVerdict }) {
  return (
    <li className="space-y-1 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <span className="text-sm font-medium text-foreground">{verdict.criterion}</span>
        <OutcomePill outcome={verdict.outcome} />
      </div>
      <p className="text-xs text-text-secondary">{verdict.evidence}</p>
    </li>
  )
}

function AttemptCard({ attempt }: { attempt: PlanEvaluationAttempt }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          Judgement {attempt.attempt} · {formatDateTime(attempt.evaluated_at)}
        </span>
        <StatusPill tone={attempt.objective_met ? 'success' : 'danger'}>
          {attempt.objective_met ? 'Objective met' : 'Objective not met'}
        </StatusPill>
      </div>
      <p className="flex items-start gap-1.5 text-sm text-text-secondary">
        <ScrollText
          className="mt-0.5 size-4 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
        {attempt.summary}
      </p>
      <ul className="flex flex-col gap-2">
        {attempt.verdicts.map((verdict) => (
          <VerdictRow key={verdict.criterion} verdict={verdict} />
        ))}
      </ul>
    </div>
  )
}

function DeliveryPill({ objectiveMet }: { objectiveMet: boolean }) {
  return (
    <StatusPill tone={objectiveMet ? 'success' : 'danger'}>
      {objectiveMet ? 'Delivered' : 'Not delivered'}
    </StatusPill>
  )
}

function EvaluationBody({
  attempts,
  loading,
  error,
}: {
  attempts: readonly PlanEvaluationAttempt[]
  loading: boolean
  error: string | null
}) {
  if (attempts.length > 0) {
    return (
      <div className="flex flex-col gap-4">
        {attempts.map((attempt) => (
          <AttemptCard key={attempt.attempt} attempt={attempt} />
        ))}
      </div>
    )
  }
  if (loading) return <Skeleton className="h-24 w-full" />
  return (
    <p className="text-xs text-muted-foreground">Delivery verdict unavailable: {error}</p>
  )
}

/**
 * The evaluate stage's verdict on the delivered initiative: every objective
 * criterion with the judge's evidence, newest judgement first. This is what
 * lets a parked initiative explain itself instead of leaving the operator with
 * a status and no account of which criteria failed.
 *
 * Hidden entirely when nothing has judged the plan yet; a fetch error surfaces
 * inline rather than blanking the workspace.
 */
export function PlanEvaluationPanel({ planId }: { planId: string }) {
  const { attempts, loading, error } = usePlanEvaluation(planId)
  if (!loading && error === null && attempts.length === 0) return null
  const latest = attempts[0]
  return (
    <SectionCard
      title="Delivery verdict"
      icon={Gavel}
      action={
        latest !== undefined ? (
          <DeliveryPill objectiveMet={latest.objective_met} />
        ) : undefined
      }
    >
      <EvaluationBody attempts={attempts} loading={loading} error={error} />
    </SectionCard>
  )
}
