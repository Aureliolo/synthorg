import { CircleCheck, ShieldQuestion, UsersRound } from 'lucide-react'

import type {
  PlanReview,
  PlanReviewerVerdict,
  PlanReviewFinding,
} from '@/api/types/plans'
import { SectionCard } from '@/components/ui/section-card'
import { StatusPill } from '@/components/ui/status-pill'
import type { StatusPillTone } from '@/components/ui/status-pill'

type Verdict = PlanReview['verdict']
type FindingCategory = PlanReviewFinding['category']

const VERDICT_LABEL: Record<Verdict, string> = {
  endorsed: 'Endorsed',
  concerns: 'Concerns',
  revision_requested: 'Revision requested',
}

const VERDICT_TONE: Record<Verdict, StatusPillTone> = {
  endorsed: 'success',
  concerns: 'warning',
  revision_requested: 'danger',
}

const CATEGORY_LABEL: Record<FindingCategory, string> = {
  gap: 'Gap',
  missing_owner: 'Missing owner',
  miscalibrated_stakes: 'Miscalibrated stakes',
  risky_decision: 'Risky decision',
  budget_concern: 'Budget concern',
  other: 'Other',
}

function VerdictPill({ verdict }: { verdict: Verdict }) {
  return <StatusPill tone={VERDICT_TONE[verdict]}>{VERDICT_LABEL[verdict]}</StatusPill>
}

function Finding({ finding }: { finding: PlanReviewFinding }) {
  return (
    <li className="flex items-start gap-1.5 text-xs text-text-secondary">
      <span className="mt-0.5 shrink-0 rounded-sm border border-border bg-surface px-1.5 py-0.5 text-micro uppercase tracking-wide text-muted-foreground">
        {CATEGORY_LABEL[finding.category]}
      </span>
      <span>{finding.detail}</span>
    </li>
  )
}

function ReviewerCard({ reviewer }: { reviewer: PlanReviewerVerdict }) {
  return (
    <div className="space-y-2 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-foreground">
          {reviewer.reviewer_role}
        </span>
        <VerdictPill verdict={reviewer.verdict} />
      </div>
      {reviewer.findings.length > 0 ? (
        <ul className="space-y-1.5">
          {reviewer.findings.map((finding, index) => (
            <Finding key={`${finding.category}-${String(index)}`} finding={finding} />
          ))}
        </ul>
      ) : (
        <p className="flex items-center gap-1.5 text-xs text-text-secondary">
          <CircleCheck className="size-3.5 shrink-0 text-success" aria-hidden="true" />
          No concerns raised.
        </p>
      )}
    </div>
  )
}

/**
 * The stakeholder panel's review of the plan: the consolidated verdict plus
 * each lead's verdict and the concerns they raised, so the human approver sees
 * who reviewed it and what they flagged. Hidden when no panel reviewed the plan.
 */
export function PlanReviewPanel({ review }: { review: PlanReview | null }) {
  if (review === null) return null
  return (
    <SectionCard
      title="Stakeholder review"
      icon={UsersRound}
      action={<VerdictPill verdict={review.verdict} />}
    >
      <div className="space-y-3">
        {review.summary !== null && (
          <p className="flex items-start gap-1.5 text-sm text-text-secondary">
            <ShieldQuestion
              className="mt-0.5 size-4 shrink-0 text-muted-foreground"
              aria-hidden="true"
            />
            {review.summary}
          </p>
        )}
        <div className="flex flex-col gap-2">
          {review.reviewers.map((reviewer) => (
            <ReviewerCard key={reviewer.reviewer_id} reviewer={reviewer} />
          ))}
        </div>
      </div>
    </SectionCard>
  )
}
