import { useCallback, useId, useMemo, useRef } from 'react'

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
        // Roving tabindex: only the selected option is in the tab sequence;
        // Arrow keys move focus + selection among the rest (WAI-ARIA APG).
        tabIndex={chosen ? 0 : -1}
        data-option-id={option.id}
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

function arrowKeyDirection(key: string): -1 | 0 | 1 {
  if (key === 'ArrowDown' || key === 'ArrowRight') return 1
  if (key === 'ArrowUp' || key === 'ArrowLeft') return -1
  return 0
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
  const options = useMemo(
    () => approval.evidence_package?.options ?? [],
    [approval.evidence_package?.options],
  )
  const headingId = useId()
  const listRef = useRef<HTMLUListElement>(null)

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (onChooseOption === undefined) return
      const direction = arrowKeyDirection(e.key)
      if (direction === 0) return
      const currentIndex = options.findIndex((o) => o.id === chosenOptionId)
      if (currentIndex === -1) return
      e.preventDefault()
      const nextIndex = (currentIndex + direction + options.length) % options.length
      const next = options[nextIndex]
      if (!next) return
      onChooseOption(next.id)
      const buttons =
        listRef.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]')
      Array.from(buttons ?? [])
        .find((btn) => btn.dataset['optionId'] === next.id)
        ?.focus()
    },
    [onChooseOption, options, chosenOptionId],
  )

  if (options.length === 0) return null
  const interactive = onChooseOption !== undefined
  return (
    <div>
      <span
        id={headingId}
        className="flex items-center gap-1.5 text-compact font-semibold uppercase tracking-wider text-muted-foreground"
      >
        <Scale className="size-3.5" aria-hidden="true" />
        Choose an option
      </span>
      <ul
        ref={listRef}
        className="mt-2 space-y-2"
        {...(interactive && {
          role: 'radiogroup',
          'aria-labelledby': headingId,
          onKeyDown: handleKeyDown,
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
