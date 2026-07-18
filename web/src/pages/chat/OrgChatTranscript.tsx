import { ArrowDown } from 'lucide-react'

import { ChatBubble } from '@/components/ui/chat-bubble'
import { StatusPill } from '@/components/ui/status-pill'

import { ChatErrorNotice } from './ChatErrorNotice'
import { ChatThinkingIndicator } from './ChatThinkingIndicator'
import type {
  OrgAssistantTurn,
  OrgEventTurn,
  OrgTurn,
} from './org-chat-types'
import { OrgEventCard } from './org-chat-events'
import type { AutoScroll } from './use-auto-scroll'

/** Sources / cited records / confidence footer for an assistant answer. */
function AssistantMeta({ turn }: { turn: OrgAssistantTurn }) {
  const cited = turn.citedRecords ?? []
  return (
    <>
      {turn.sources && turn.sources.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Sources: {turn.sources.join(', ')}
        </p>
      )}
      {cited.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {cited.map((record) => (
            <StatusPill
              key={`${record.kind}:${record.record_id}`}
              tone="text-secondary"
            >
              <span className="font-medium capitalize">{record.kind}</span>
              {': '}
              <span>{record.label}</span>{' '}
              <span className="opacity-70">({record.status})</span>
            </StatusPill>
          ))}
        </div>
      )}
      {typeof turn.confidence === 'number' && (
        <p className="text-xs text-muted-foreground">
          Confidence: {Math.round(turn.confidence * 100)}%
        </p>
      )}
    </>
  )
}

interface EventProps {
  resolvingInvites: ReadonlySet<string>
  onResolveInvite: (turnId: number, approvalId: string, accept: boolean) => void
}

function EventTurnView({ turn, events }: { turn: OrgEventTurn; events: EventProps }) {
  return (
    <OrgEventCard
      turnId={turn.id}
      event={turn.event}
      resolvingInvites={events.resolvingInvites}
      onResolveInvite={events.onResolveInvite}
    />
  )
}

interface TurnViewProps {
  turn: OrgTurn
  onRetry: (turnId: number) => void
  events: EventProps
}

function TurnView({ turn, onRetry, events }: TurnViewProps) {
  switch (turn.kind) {
    case 'human':
      return (
        <ChatBubble variant="human" content={turn.content} timestamp={turn.timestamp} />
      )
    case 'assistant':
      if (turn.isError === true) {
        return (
          <div className="mr-8">
            <ChatErrorNotice message={turn.content} onRetry={() => onRetry(turn.id)} />
          </div>
        )
      }
      return (
        <ChatBubble
          variant="assistant"
          content={turn.content}
          roleLabel={turn.roleLabel}
          timestamp={turn.timestamp}
        >
          <AssistantMeta turn={turn} />
        </ChatBubble>
      )
    case 'agent':
      return (
        <ChatBubble
          variant="agent"
          content={turn.content}
          agentName={turn.agentName}
          agentRole={turn.agentRole}
          agentTopic={turn.agentTopic}
          timestamp={turn.timestamp}
        />
      )
    case 'notice':
      if (turn.isError === true) {
        return (
          <div className="mx-4">
            <ChatErrorNotice message={turn.content} onRetry={() => onRetry(turn.id)} />
          </div>
        )
      }
      return (
        <div className="mx-4 rounded-md bg-muted/50 p-card text-xs text-muted-foreground">
          {turn.content}
        </div>
      )
    case 'event':
      return <EventTurnView turn={turn} events={events} />
  }
}

export interface OrgChatTranscriptProps {
  messages: readonly OrgTurn[]
  sending: boolean
  autoScroll: AutoScroll
  resolvingInvites: ReadonlySet<string>
  onResolveInvite: (turnId: number, approvalId: string, accept: boolean) => void
  onRetry: (turnId: number) => void
}

/** The scrolling transcript of the unified org conversation. */
export function OrgChatTranscript({
  messages,
  sending,
  autoScroll,
  resolvingInvites,
  onResolveInvite,
  onRetry,
}: OrgChatTranscriptProps) {
  const events: EventProps = { resolvingInvites, onResolveInvite }
  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={autoScroll.scrollRef}
        role="log"
        aria-label="Organisation conversation"
        aria-busy={sending}
        className="h-full space-y-3 overflow-y-auto rounded-md border border-border p-card"
      >
        {messages.map((turn) => (
          <TurnView key={turn.id} turn={turn} onRetry={onRetry} events={events} />
        ))}
        {sending && <ChatThinkingIndicator label="The org is responding" />}
      </div>
      {autoScroll.showJumpToLatest && (
        <button
          type="button"
          onClick={autoScroll.jumpToLatest}
          className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-full border border-border bg-card px-3 py-1 text-xs text-foreground shadow-md hover:bg-card-hover focus-visible:ring-2 focus-visible:ring-accent focus-visible:outline-none"
        >
          <ArrowDown className="size-3.5" aria-hidden />
          Jump to latest
        </button>
      )}
    </div>
  )
}
