import { useState } from 'react'

import { CheckCircle2, HelpCircle, Lightbulb, RefreshCw, Send } from 'lucide-react'

import type { Plan } from '@/api/types/plans'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SectionCard } from '@/components/ui/section-card'
import { StatusPill } from '@/components/ui/status-pill'
import { DECISION_TEXT_MAX } from '@/utils/approvals'
import { answeredQuestions, type QuestionAnswer } from '@/utils/planCoverage'
import { usePlanQuestions, type PlanQuestionsController } from './usePlanQuestions'

// Mirrors ApproveRequest.comment's server bound, so an over-long answer is
// capped here rather than rejected after a round trip.
const ANSWER_MAX = DECISION_TEXT_MAX

/** The answer box for one question the plan has parked for a person. */
function QuestionAnswerForm({
  approvalId,
  questions,
}: {
  approvalId: string
  questions: PlanQuestionsController
}) {
  const [draft, setDraft] = useState('')
  const submitting = questions.isSubmitting(approvalId)
  const answer = draft.trim()
  return (
    <form
      className="mt-1 flex items-end gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        if (answer === '') return
        void questions.answer(approvalId, answer)
      }}
    >
      <div className="flex-1">
        <InputField
          label="Your answer"
          value={draft}
          maxLength={ANSWER_MAX}
          onValueChange={setDraft}
        />
      </div>
      <Button type="submit" size="sm" disabled={submitting || answer === ''}>
        <Send aria-hidden="true" />
        {submitting ? 'Sending…' : 'Send answer'}
      </Button>
    </form>
  )
}

/**
 * One open question, with the answer box when the plan is still holding it
 * open for a person.
 *
 * A question with no parked approval is one the plan already stopped waiting
 * on: it is closed by omission the moment the plan starts building, and the
 * agents were briefed that nobody answered. Saying so is the point, because
 * offering an answer box that settles nothing is the dead end this replaced.
 */
function OpenQuestion({
  question,
  occurrence,
  questions,
}: {
  question: string
  occurrence: number
  questions: PlanQuestionsController
}) {
  const approvalId = questions.approvalFor(question, occurrence)
  return (
    <li className="space-y-1">
      <div className="flex items-start gap-1.5 text-sm text-foreground">
        <HelpCircle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden="true" />
        {question}
      </div>
      {approvalId !== undefined && (
        <QuestionAnswerForm approvalId={approvalId} questions={questions} />
      )}
      {approvalId === undefined && !questions.resolving && !questions.lookupFailed && (
        <p className="text-xs text-muted-foreground">
          No longer answerable: the plan stopped waiting and is proceeding on its
          own assumption.
        </p>
      )}
    </li>
  )
}

/**
 * How many times this question has already appeared above *index*.
 *
 * The parked approvals for one repeated question are queued in order, so the
 * n-th rendering of it takes the n-th queued approval and each row answers
 * its own.
 */
function occurrenceOf(questions: readonly QuestionAnswer[], index: number): number {
  const text = questions[index]?.question
  let seen = 0
  for (let i = 0; i < index; i += 1) {
    if (questions[i]?.question === text) seen += 1
  }
  return seen
}

function OpenQuestions({
  questions,
  controller,
}: {
  questions: readonly QuestionAnswer[]
  controller: PlanQuestionsController
}) {
  return (
    <div className="space-y-1.5">
      <span className="text-micro uppercase tracking-wide text-muted-foreground">
        Open questions
      </span>
      <ul className="space-y-2">
        {/* Keyed and resolved by position, not by text: the same question may
            legitimately appear twice, and keying on the text alone gives React
            duplicate keys and points both rows at one approval. */}
        {questions.map(({ question }, index) => (
          <OpenQuestion
            key={`${question}#${String(index)}`}
            question={question}
            occurrence={occurrenceOf(questions, index)}
            questions={controller}
          />
        ))}
      </ul>
      {controller.lookupFailed && (
        <div className="flex items-center gap-2 text-xs text-danger">
          Could not load these questions, so they cannot be answered right now.
          <Button variant="outline" size="sm" onClick={() => void controller.retry()}>
            <RefreshCw className="size-3.5" aria-hidden="true" />
            Retry
          </Button>
        </div>
      )}
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
        {questions.map(({ question, settledBy }, index) => (
          <li
            key={`${question}#${String(index)}`}
            className="flex items-start gap-1.5 text-xs text-text-secondary"
          >
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
 *
 * Each remaining question is answered here, because here is the only place it
 * can be: the generic Approvals inbox excludes every ``plan_review`` row by
 * design, so a question sent anywhere else is a question nobody can decide.
 */
export function PlanOpenQuestionsPanel({ plan }: { plan: Plan }) {
  const { open_questions: questions, assumptions } = plan
  const controller = usePlanQuestions(plan.id, questions)
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
        {open.length > 0 && <OpenQuestions questions={open} controller={controller} />}
        {settled.length > 0 && <SettledQuestions questions={settled} />}
        {assumptions.length > 0 && <Assumptions assumptions={assumptions} />}
      </div>
    </SectionCard>
  )
}
