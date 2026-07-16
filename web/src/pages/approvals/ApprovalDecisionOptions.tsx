import { Scale, Sparkles } from 'lucide-react'

import { StatusPill } from '@/components/ui/status-pill'
import { cn } from '@/lib/utils'
import type { ApprovalResponse } from '@/api/types/approvals'
import type { PlanOption } from '@/api/types/plans'

/** Accent highlight for the selected / recommended decision option. */
const DECISION_HIGHLIGHT = 'border-accent/40 bg-accent/[0.04]'

/** Title + recommended/selected pills + the option's tradeoff writeup. */
function DecisionOptionBody({
  option,
  chosen,
}: {
  option: PlanOption
  chosen: boolean
}) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-foreground">{option.title}</span>
        {option.recommended && (
          <StatusPill tone="accent" icon={Sparkles}>
            Recommended
          </StatusPill>
        )}
        {chosen && <StatusPill tone="success">Selected</StatusPill>}
      </div>
      <p className="mt-1 text-xs text-text-secondary">{option.summary}</p>
    </>
  )
}

function DecisionOptionRow({
  option,
  chosen,
  onChoose,
}: {
  option: PlanOption
  chosen: boolean
  onChoose: ((id: string) => void) | undefined
}) {
  const body = <DecisionOptionBody option={option} chosen={chosen} />
  if (onChoose === undefined) {
    return (
      <li className={cn('rounded-md border p-card', chosen ? DECISION_HIGHLIGHT : 'border-border')}>
        {body}
      </li>
    )
  }
  return (
    <li>
      <button
        type="button"
        role="radio"
        aria-checked={chosen}
        onClick={() => onChoose(option.id)}
        className={cn(
          'w-full rounded-md border p-card text-left transition-colors',
          chosen ? DECISION_HIGHLIGHT : 'border-border hover:border-bright hover:bg-card-hover',
        )}
      >
        {body}
      </button>
    </li>
  )
}

/**
 * The execution-time decision fork an agent parked: each option's tradeoffs +
 * the recommendation. When ``onChooseOption`` is given the operator picks one
 * (the choice rides back as the decision the agent resumes with); otherwise the
 * options render read-only.
 */
export function DecisionOptionsSection({
  approval,
  chosenOptionId,
  onChooseOption,
}: {
  approval: ApprovalResponse
  chosenOptionId: string | null
  onChooseOption: ((id: string) => void) | undefined
}) {
  const options = approval.evidence_package?.options ?? []
  if (options.length === 0) return null
  return (
    <div>
      <span className="flex items-center gap-1.5 text-compact font-semibold uppercase tracking-wider text-muted-foreground">
        <Scale className="size-3.5" aria-hidden="true" />
        Choose an option
      </span>
      <ul
        className="mt-2 space-y-2"
        {...(onChooseOption !== undefined && {
          role: 'radiogroup',
          'aria-label': 'Decision options',
        })}
      >
        {options.map((option) => (
          <DecisionOptionRow
            key={option.id}
            option={option}
            chosen={option.id === chosenOptionId}
            onChoose={onChooseOption}
          />
        ))}
      </ul>
    </div>
  )
}
