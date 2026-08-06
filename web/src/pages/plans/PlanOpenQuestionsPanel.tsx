import { CheckCircle2, HelpCircle, Lightbulb, MessagesSquare } from 'lucide-react'
import { Link } from 'react-router'

import type { Plan } from '@/api/types/plans'
import { Button } from '@/components/ui/button'
import { SectionCard } from '@/components/ui/section-card'
import { StatusPill } from '@/components/ui/status-pill'
import { ROUTES } from '@/router/routes'
import { answeredQuestions, type QuestionAnswer } from '@/utils/plans'

function OpenQuestions({ questions }: { questions: readonly QuestionAnswer[] }) {
  return (
    <div className="space-y-1.5">
      <span className="text-micro uppercase tracking-wide text-muted-foreground">
        Open questions
      </span>
      <ul className="space-y-1.5">
        {questions.map(({ question }) => (
          <li key={question} className="flex items-start gap-1.5 text-sm text-foreground">
            <HelpCircle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden="true" />
            {question}
          </li>
        ))}
      </ul>
      {/* The answering path is chat, and the panel used to be read-only with no
          hint of that: an operator could see what was wanted and not where to
          say it. */}
      <Button asChild variant="outline" size="sm" className="mt-1">
        <Link to={ROUTES.CHAT}>
          <MessagesSquare className="size-3.5" aria-hidden="true" />
          Answer in chat
        </Link>
      </Button>
    </div>
  )
}

function SettledQuestions({ questions }: { questions: readonly QuestionAnswer[] }) {
  return (
    <div className="space-y-1.5">
      <span className="text-micro uppercase tracking-wide text-muted-foreground">
        Already answered by the plan
      </span>
      <ul className="space-y-1.5">
        {questions.map(({ question, settledBy }) => (
          <li key={question} className="flex items-start gap-1.5 text-xs text-text-secondary">
            <CheckCircle2
              className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
              aria-hidden="true"
            />
            <span>
              {question}{' '}
              <span className="text-muted-foreground">(settled by {settledBy})</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Assumptions({ assumptions }: { assumptions: readonly string[] }) {
  return (
    <div className="space-y-1.5">
      <span className="text-micro uppercase tracking-wide text-muted-foreground">
        Assumptions
      </span>
      <ul className="space-y-1.5">
        {assumptions.map((assumption) => (
          <li key={assumption} className="flex items-start gap-1.5 text-xs text-text-secondary">
            <Lightbulb
              className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
              aria-hidden="true"
            />
            {assumption}
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * What the planner needs from the human before approval: the open questions it
 * could not resolve (flagged for an answer) and the load-bearing assumptions the
 * plan rests on (so a wrong one can be corrected). Hidden when the plan surfaced
 * neither.
 *
 * A question an item's acceptance criteria already settle is moved out of the
 * ask rather than dropped: it stops demanding an answer and stops inflating the
 * count, while a wrong match still costs only a glance.
 */
export function PlanOpenQuestionsPanel({ plan }: { plan: Plan }) {
  const { open_questions: questions, assumptions } = plan
  const paired = answeredQuestions(questions, plan.items)
  const open = paired.filter((entry) => entry.settledBy === null)
  const settled = paired.filter((entry) => entry.settledBy !== null)
  if (questions.length === 0 && assumptions.length === 0) return null
  return (
    <SectionCard
      title="Needs your input"
      icon={HelpCircle}
      action={
        open.length > 0 ? (
          <StatusPill tone="warning">
            {open.length} open question{open.length === 1 ? '' : 's'}
          </StatusPill>
        ) : undefined
      }
    >
      <div className="space-y-3">
        {open.length > 0 && <OpenQuestions questions={open} />}
        {settled.length > 0 && <SettledQuestions questions={settled} />}
        {assumptions.length > 0 && <Assumptions assumptions={assumptions} />}
      </div>
    </SectionCard>
  )
}
