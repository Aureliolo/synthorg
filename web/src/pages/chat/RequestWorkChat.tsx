import { ClipboardList } from 'lucide-react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { ChatInputArea } from '@/components/ui/chat-input-area'
import { EmptyState } from '@/components/ui/empty-state'
import { ExamplePrompts } from '@/components/ui/example-prompts'
import { ResponderAttribution } from '@/components/ui/responder-attribution'
import { cn } from '@/lib/utils'
import { approvalDetailPath } from '@/utils/approvals'

import { hasAttribution } from './attribution'
import { ChatErrorNotice } from './ChatErrorNotice'
import { ChatThinkingIndicator } from './ChatThinkingIndicator'
import { useRequestWorkState, type RequestWorkMessage } from './useRequestWorkState'

const INPUT_LABEL = 'Work request'
const INPUT_PLACEHOLDER = 'Describe work for the organisation...'

const EXAMPLE_PROMPTS: readonly string[] = [
  'Write a competitive analysis of our three closest competitors.',
  'Draft a launch announcement for the new feature and have marketing review it.',
  'Investigate why task throughput dropped this week and propose fixes.',
]

interface ProposeBubbleProps {
  msg: RequestWorkMessage
  onRetry: () => void
}

function ApprovalLink({ id, label }: { id: string; label: string }) {
  return (
    <li>
      <Link
        to={approvalDetailPath(id)}
        className="underline underline-offset-2 hover:text-foreground"
      >
        {label}
      </Link>
    </li>
  )
}

function QueuedApprovals({ msg }: { msg: RequestWorkMessage }) {
  if (msg.role !== 'assistant') return null
  const proposals = msg.proposals ?? []
  const steering = msg.steering ?? []
  if (proposals.length === 0 && steering.length === 0) return null
  return (
    <div className="mt-2 space-y-2 text-xs text-text-secondary">
      {proposals.length > 0 && (
        <div>
          <p className="font-medium text-foreground">Approve to start</p>
          <p className="text-micro text-muted-foreground">
            This is the first gate: approving queues the work. Once it runs, a
            separate review of the result appears in Approvals.
          </p>
          <ul className="mt-1 list-disc pl-4">
            {proposals.map((p) => (
              <ApprovalLink key={p.approvalId} id={p.approvalId} label={p.title} />
            ))}
          </ul>
        </div>
      )}
      {steering.length > 0 && (
        <div>
          <p className="font-medium text-foreground">Confirm steering</p>
          <ul className="mt-1 list-disc pl-4">
            {steering.map((s) => (
              <ApprovalLink key={s.approvalId} id={s.approvalId} label={s.text} />
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function ProposeReplyBubble({ msg }: { msg: RequestWorkMessage }) {
  return (
    <div
      className={cn(
        'rounded-md p-card text-sm text-foreground',
        msg.role === 'user' ? 'ml-8 bg-accent/10' : 'mr-8 bg-card',
      )}
    >
      <p className="whitespace-pre-wrap">{msg.content}</p>
      <QueuedApprovals msg={msg} />
      {msg.role === 'assistant' &&
        hasAttribution(msg.responderName, msg.responderRole) && (
          <ResponderAttribution
            name={msg.responderName ?? ''}
            role={msg.responderRole ?? ''}
            topic={msg.routedTopic}
          />
        )}
    </div>
  )
}

function ProposeBubble({ msg, onRetry }: ProposeBubbleProps) {
  if (msg.role === 'assistant' && msg.isError === true) {
    return (
      <div className="mr-8">
        <ChatErrorNotice message={msg.content} onRetry={onRetry} />
      </div>
    )
  }
  return <ProposeReplyBubble msg={msg} />
}

export function RequestWorkChat() {
  const ctrl = useRequestWorkState()
  const sendDisabled = ctrl.proposeLoading || ctrl.conversationClosed

  if (ctrl.messages.length === 0 && !ctrl.proposeLoading) {
    return (
      <div className="space-y-section-gap">
        <EmptyState
          icon={ClipboardList}
          title="Request work"
          description="Describe work in natural language. The Chief of Staff clarifies, then queues concrete items for your approval; concern-routed turns are answered by the matching role agent."
        />
        <ExamplePrompts
          prompts={EXAMPLE_PROMPTS}
          onSelect={ctrl.setInput}
          disabled={ctrl.proposeLoading}
        />
        <ChatInputArea
          value={ctrl.input}
          onChange={ctrl.setInput}
          onSend={ctrl.triggerSend}
          disabled={ctrl.proposeLoading}
          label={INPUT_LABEL}
          placeholder={INPUT_PLACEHOLDER}
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-section-gap">
      <div
        ref={ctrl.scrollRef}
        role="log"
        aria-label="Clarify and propose conversation"
        className="max-h-80 space-y-3 overflow-y-auto rounded-md border border-border p-card"
      >
        {ctrl.messages.map((msg) => (
          <ProposeBubble
            key={msg.id}
            msg={msg}
            onRetry={() => {
              ctrl.retryBefore(msg.id)
            }}
          />
        ))}
        {ctrl.proposeLoading && (
          <div className="flex items-center justify-between gap-3">
            <ChatThinkingIndicator label="Working on it" />
            <Button variant="ghost" size="sm" onClick={ctrl.cancel}>
              Cancel
            </Button>
          </div>
        )}
      </div>

      {ctrl.conversationClosed && (
        <div className="flex items-center justify-between gap-3">
          <p role="status" className="text-xs text-text-secondary">
            This request-work conversation is closed.
          </p>
          <Button variant="outline" size="sm" onClick={ctrl.startNew}>
            Start new conversation
          </Button>
        </div>
      )}

      <ChatInputArea
        value={ctrl.input}
        onChange={ctrl.setInput}
        onSend={ctrl.triggerSend}
        disabled={sendDisabled}
        inputDisabled={ctrl.conversationClosed}
        label={INPUT_LABEL}
        placeholder={INPUT_PLACEHOLDER}
      />
    </div>
  )
}
