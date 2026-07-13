import { HelpCircle, Lightbulb } from 'lucide-react'

import type { Plan } from '@/api/types/plans'
import { SectionCard } from '@/components/ui/section-card'
import { StatusPill } from '@/components/ui/status-pill'

/**
 * What the planner needs from the human before approval: the open questions it
 * could not resolve (flagged for an answer) and the load-bearing assumptions the
 * plan rests on (so a wrong one can be corrected). Hidden when the plan surfaced
 * neither.
 */
export function PlanOpenQuestionsPanel({ plan }: { plan: Plan }) {
  const { open_questions: questions, assumptions } = plan
  if (questions.length === 0 && assumptions.length === 0) return null
  return (
    <SectionCard
      title="Needs your input"
      icon={HelpCircle}
      action={
        questions.length > 0 ? (
          <StatusPill tone="warning">
            {questions.length} open question{questions.length === 1 ? '' : 's'}
          </StatusPill>
        ) : undefined
      }
    >
      <div className="space-y-3">
        {questions.length > 0 && (
          <div className="space-y-1.5">
            <span className="text-micro uppercase tracking-wide text-muted-foreground">
              Open questions
            </span>
            <ul className="space-y-1.5">
              {questions.map((question) => (
                <li
                  key={question}
                  className="flex items-start gap-1.5 text-sm text-foreground"
                >
                  <HelpCircle
                    className="mt-0.5 size-3.5 shrink-0 text-warning"
                    aria-hidden="true"
                  />
                  {question}
                </li>
              ))}
            </ul>
          </div>
        )}
        {assumptions.length > 0 && (
          <div className="space-y-1.5">
            <span className="text-micro uppercase tracking-wide text-muted-foreground">
              Assumptions
            </span>
            <ul className="space-y-1.5">
              {assumptions.map((assumption) => (
                <li
                  key={assumption}
                  className="flex items-start gap-1.5 text-xs text-text-secondary"
                >
                  <Lightbulb
                    className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                  {assumption}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </SectionCard>
  )
}
