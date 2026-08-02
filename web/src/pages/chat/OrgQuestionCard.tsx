import { AlertTriangle, HelpCircle } from 'lucide-react'
import { useCallback, useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { ChatInputArea } from '@/components/ui/chat-input-area'
import { StatusPill } from '@/components/ui/status-pill'
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
 */

const CARD = 'mr-8 space-y-2 rounded-md border border-border bg-card-hover p-card'
const HARD_CARD =
  'mr-8 space-y-2 rounded-md border border-warning/40 bg-card-hover p-card'

const ANSWER_LABEL = 'Answer the question'
const ANSWER_PLACEHOLDER = 'Answer so the agent can carry on...'
// The composer at the foot of the page also renders a send button, so the two
// must not share an accessible name.
const SEND_LABEL = 'Send answer'

const DECLINE_HINT =
  'Decline: the agent resumes and proceeds on its own judgement, stating the assumption it made.'

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
  approvalId,
  resolving,
  onDecline,
}: {
  approvalId: string
  resolving: boolean
  onDecline: (approvalId: string) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        size="sm"
        variant="outline"
        disabled={resolving}
        aria-busy={resolving}
        onClick={() => onDecline(approvalId)}
      >
        Decline
      </Button>
      <Button asChild variant="link" size="sm" className="h-auto p-0">
        <Link to={approvalDetailPath(approvalId)}>Review in Approvals</Link>
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
  onAnswer: (approvalId: string, answer: string) => void
  onDecline: (approvalId: string) => void
}) {
  const [draft, setDraft] = useState('')
  const send = useCallback(() => {
    const answer = draft.trim()
    if (!answer) return
    onAnswer(event.approvalId, answer)
  }, [draft, event.approvalId, onAnswer])

  return (
    <div className="space-y-2">
      <ChatInputArea
        value={draft}
        onChange={setDraft}
        onSend={send}
        disabled={resolving || draft.trim().length === 0}
        label={ANSWER_LABEL}
        placeholder={ANSWER_PLACEHOLDER}
        sendLabel={SEND_LABEL}
        rows={2}
      />
      <p className="text-micro text-muted-foreground">{DECLINE_HINT}</p>
      <DeclineRow
        approvalId={event.approvalId}
        resolving={resolving}
        onDecline={onDecline}
      />
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
  onAnswer: (approvalId: string, answer: string, chosenOptionId: string) => void
  onDecline: (approvalId: string) => void
}) {
  const pick = useCallback(
    (option: QuestionOption) => {
      onAnswer(event.approvalId, option.title, option.id)
    },
    [event.approvalId, onAnswer],
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
      <DeclineRow
        approvalId={event.approvalId}
        resolving={resolving}
        onDecline={onDecline}
      />
    </div>
  )
}

export interface OrgQuestionCardProps {
  event: QuestionEvent
  resolving: boolean
  onAnswer: (approvalId: string, answer: string, chosenOptionId?: string) => void
  onDecline: (approvalId: string) => void
}

/** Render one parked agent question, answerable in place. */
export function OrgQuestionCard({
  event,
  resolving,
  onAnswer,
  onDecline,
}: OrgQuestionCardProps) {
  return (
    <div className={event.hardToReverse ? HARD_CARD : CARD}>
      <QuestionHeader event={event} />
      {event.options.length > 0 ? (
        <DecisionBody
          event={event}
          resolving={resolving}
          onAnswer={onAnswer}
          onDecline={onDecline}
        />
      ) : (
        <ClarifyBody
          event={event}
          resolving={resolving}
          onAnswer={onAnswer}
          onDecline={onDecline}
        />
      )}
    </div>
  )
}
