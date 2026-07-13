import { useState } from 'react'

import { CircleCheck, GitBranch, Package, Scale, Sparkles, UserRound } from 'lucide-react'

import type { PlanItem } from '@/api/types/plans'
import { Button } from '@/components/ui/button'
import { StatusPill } from '@/components/ui/status-pill'
import { cn } from '@/lib/utils'
import { COMPLEXITY_LABEL, COMPLEXITY_TONE, STAKES_LABEL, STAKES_TONE } from '@/utils/plans'

/** Accent highlight for a card that wants attention (chosen / critical path). */
export const ACCENT_HIGHLIGHT = 'border-accent/40 bg-accent/[0.04]'

export function ItemPills({
  item,
  onCriticalPath,
}: {
  item: PlanItem
  onCriticalPath: boolean
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {item.kind === 'decision' && (
        <StatusPill tone="accent" icon={Scale}>
          Decision
        </StatusPill>
      )}
      <StatusPill tone={COMPLEXITY_TONE[item.estimated_complexity]}>
        {COMPLEXITY_LABEL[item.estimated_complexity]} effort
      </StatusPill>
      <StatusPill tone={STAKES_TONE[item.stakes]}>
        {STAKES_LABEL[item.stakes]} stakes
      </StatusPill>
      {onCriticalPath && (
        <StatusPill tone="accent" icon={GitBranch}>
          Critical path
        </StatusPill>
      )}
      {item.owner === null && (
        <StatusPill tone="warning" icon={UserRound}>
          Unassigned
        </StatusPill>
      )}
    </div>
  )
}

export function AcceptanceCriteria({ criteria }: { criteria: readonly string[] }) {
  if (criteria.length === 0) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-warning">
        <CircleCheck className="size-3.5 shrink-0" aria-hidden="true" />
        No acceptance criteria: nothing defines when this item is done.
      </p>
    )
  }
  return (
    <div>
      <span className="text-micro uppercase tracking-wide text-muted-foreground">
        Done when
      </span>
      <ul className="mt-1 space-y-1">
        {criteria.map((line) => (
          <li key={line} className="flex items-start gap-1.5 text-xs text-text-secondary">
            <CircleCheck
              className="mt-0.5 size-3.5 shrink-0 text-success"
              aria-hidden="true"
            />
            <span>{line}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function DecisionOptions({
  item,
  onChooseOption,
}: {
  item: PlanItem
  onChooseOption: ((itemId: string, optionId: string) => Promise<unknown>) | undefined
}) {
  const [pendingId, setPendingId] = useState<string | null>(null)
  if (item.kind !== 'decision') return null

  const choose = async (optionId: string): Promise<void> => {
    if (onChooseOption === undefined) return
    setPendingId(optionId)
    try {
      await onChooseOption(item.id, optionId)
    } finally {
      setPendingId(null)
    }
  }

  return (
    <div className="space-y-1.5">
      <span className="inline-flex items-center gap-1 text-micro uppercase tracking-wide text-muted-foreground">
        <Scale className="size-3.5" aria-hidden="true" />
        Options
      </span>
      <ul className="space-y-1.5">
        {item.options.map((option) => {
          const chosen = item.chosen_option_id === option.id
          const isPending = pendingId === option.id
          return (
            <li
              key={option.id}
              className={cn('rounded-md border p-2', chosen ? ACCENT_HIGHLIGHT : 'border-border')}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-foreground">{option.title}</span>
                {option.recommended && (
                  <StatusPill tone="accent" icon={Sparkles}>
                    Recommended
                  </StatusPill>
                )}
                {chosen && <StatusPill tone="success">Chosen</StatusPill>}
                {onChooseOption !== undefined && !chosen && (
                  <Button
                    variant="ghost"
                    size="xs"
                    className="ml-auto"
                    aria-label={`Choose ${option.title}`}
                    disabled={pendingId !== null}
                    onClick={() => void choose(option.id)}
                  >
                    {isPending ? 'Choosing…' : 'Choose'}
                  </Button>
                )}
              </div>
              <p className="mt-1 text-xs text-text-secondary">{option.summary}</p>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export function ChipRow({
  label,
  icon: Icon,
  values,
}: {
  label: string
  icon: typeof Package
  values: readonly string[]
}) {
  if (values.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="inline-flex items-center gap-1 text-micro uppercase tracking-wide text-muted-foreground">
        <Icon className="size-3.5" aria-hidden="true" />
        {label}
      </span>
      {values.map((value) => (
        <span
          key={value}
          className="rounded-sm border border-border bg-surface px-1.5 py-0.5 text-micro text-text-secondary"
        >
          {value}
        </span>
      ))}
    </div>
  )
}
