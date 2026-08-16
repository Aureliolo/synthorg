import { AlertTriangle, HelpCircle } from 'lucide-react'
import { useCallback, useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { ChatInputArea } from '@/components/ui/chat-input-area'
import { StatusPill } from '@/components/ui/status-pill'
import { useOrgQuestionsStore } from '@/stores/org-questions'
import { approvalDetailPath } from '@/utils/approvals'
import { formatRelativeTime } from '@/utils/format'

import type { QuestionEvent, QuestionOption } from './org-chat-types'

/**
 * The org asked a question and is waiting. Answering here resumes the run;
 * declining resumes it on the agent's own judgement.
 *
 * A clarification takes free text. A project decision takes a structural pick
 * instead, because the server resolves the chosen option's writeup as what the
 * agent continues with, so free text there would be silently dropped.
 *
 * The card reads the questions store directly rather than taking the actions
 * and the resolving set through the transcript: they were threaded five levels
 * for one leaf, and subscribing here scopes a resolve's re-render to the card
 * being resolved instead of every open card.
 */

const CARD = 'mr-8 space-y-2 rounded-md border border-border bg-card-hover p-card'
const HARD_CARD =
  'mr-8 space-y-2 rounded-md border border-warning/40 bg-card-hover p-card'

/** Backend cap on ``AnswerQuestionRequest.answer``; over it the POST 400s. */
const ANSWER_MAX_LENGTH = 4096

const ANSWER_PLACEHOLDER = 'Answer so the agent can carry on...'

const DECLINE_HINT =
  'Decline: the agent resumes and proceeds on its own judgement, stating the assumption it made.'

/**
 * Name a control by the question it acts on.
 *
 * Several questions can be open at once, and the composer at the foot of the
 * page renders its own send button, so a bare "Send answer" leaves a
 * screen-reader user with a list of identically named controls and no way to
 * tell which question they are about to answer.
 */
function labelFor(action: string, event: QuestionEvent): string {
  return `${action}: ${event.askedByName} asks "${event.question}"`
}

function QuestionHeader({ event }: { event: QuestionEvent }) {
  const context = [event.taskTitle, event.project].filter(Boolean).join(' - ')
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <HelpCircle className="size-4 text-accent" aria-hidden />
        <span className="text-sm font-medium text-foreground">
          {event.askedByName} is asking
        </span>
        {event.hardToReverse && (
          <StatusPill tone="warning" icon={AlertTriangle}>
            Hard to reverse
          </StatusPill>
        )}
        <span className="text-micro text-muted-foreground">
          {formatRelativeTime(event.askedAt)}
        </span>
      </div>
      {context && <p className="text-xs text-muted-foreground">{context}</p>}
      <p className="whitespace-pre-wrap text-sm text-foreground">{event.question}</p>
    </div>
  )
}

function DeclineRow({
  event,
  resolving,
  onDecline,
}: {
  event: QuestionEvent
  resolving: boolean
  onDecline: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        size="sm"
        variant="outline"
        disabled={resolving}
        aria-busy={resolving}
        aria-label={labelFor('Decline', event)}
        onClick={onDecline}
      >
        Decline
      </Button>
      <Button asChild variant="link" size="sm" className="h-auto p-0">
        <Link
          to={approvalDetailPath(event.approvalId)}
          aria-label={labelFor('Review in Approvals', event)}
        >
          Review in Approvals
        </Link>
      </Button>
    </div>
  )
}

function ClarifyBody({
  event,
  resolving,
  onAnswer,
  onDecline,
}: {
  event: QuestionEvent
  resolving: boolean
  onAnswer: (answer: string, chosenOptionId?: string) => void
  onDecline: () => void
}) {
  const [draft, setDraft] = useState('')
  const send = useCallback(() => {
    const answer = draft.trim()
    if (!answer) return
    onAnswer(answer)
  }, [draft, onAnswer])

  return (
    <div className="space-y-2">
      <ChatInputArea
        value={draft}
        onChange={setDraft}
        onSend={send}
        disabled={resolving || draft.trim().length === 0}
        label={labelFor('Answer', event)}
        // The card's own heading already names the question on screen, so the
        // disambiguator would read as the question restated under itself.
        hideLabel
        placeholder={ANSWER_PLACEHOLDER}
        sendLabel={labelFor('Send answer', event)}
        maxLength={ANSWER_MAX_LENGTH}
        rows={2}
      />
      <p className="text-micro text-muted-foreground">{DECLINE_HINT}</p>
      <DeclineRow event={event} resolving={resolving} onDecline={onDecline} />
    </div>
  )
}

function OptionRow({
  option,
  resolving,
  onPick,
}: {
  option: QuestionOption
  resolving: boolean
  onPick: (option: QuestionOption) => void
}) {
  return (
    <li className="space-y-1 rounded-md border border-border bg-surface p-card">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-foreground">{option.title}</span>
        {option.recommended && <StatusPill tone="accent">Recommended</StatusPill>}
      </div>
      <p className="text-xs text-muted-foreground">{option.summary}</p>
      <Button
        size="sm"
        disabled={resolving}
        aria-busy={resolving}
        onClick={() => onPick(option)}
      >
        Choose {option.title}
      </Button>
    </li>
  )
}

function DecisionBody({
  event,
  resolving,
  onAnswer,
  onDecline,
}: {
  event: QuestionEvent
  resolving: boolean
  onAnswer: (answer: string, chosenOptionId?: string) => void
  onDecline: () => void
}) {
  const pick = useCallback(
    (option: QuestionOption) => {
      onAnswer(option.title, option.id)
    },
    [onAnswer],
  )
  return (
    <div className="space-y-2">
      <ul className="space-y-2">
        {event.options.map((option) => (
          <OptionRow
            key={option.id}
            option={option}
            resolving={resolving}
            onPick={pick}
          />
        ))}
      </ul>
      <p className="text-micro text-muted-foreground">{DECLINE_HINT}</p>
      <DeclineRow event={event} resolving={resolving} onDecline={onDecline} />
    </div>
  )
}

export interface OrgQuestionCardProps {
  event: QuestionEvent
}

/** Render one parked agent question, answerable in place. */
export function OrgQuestionCard({ event }: OrgQuestionCardProps) {
  const resolving = useOrgQuestionsStore((s) => s.resolving.has(event.approvalId))
  const answerQuestion = useOrgQuestionsStore((s) => s.answerQuestion)
  const declineQuestion = useOrgQuestionsStore((s) => s.declineQuestion)

  const onAnswer = useCallback(
    (answer: string, chosenOptionId?: string) => {
      void answerQuestion(event.approvalId, answer, chosenOptionId)
    },
    [answerQuestion, event.approvalId],
  )
  const onDecline = useCallback(() => {
    void declineQuestion(event.approvalId)
  }, [declineQuestion, event.approvalId])

  const Body = event.isDecision ? DecisionBody : ClarifyBody
  return (
    <div className={event.hardToReverse ? HARD_CARD : CARD}>
      <QuestionHeader event={event} />
      <Body
        event={event}
        resolving={resolving}
        onAnswer={onAnswer}
        onDecline={onDecline}
      />
    </div>
  )
}
