import { ClipboardList } from 'lucide-react'

import { ChatInputArea } from '@/components/ui/chat-input-area'
import { EmptyState } from '@/components/ui/empty-state'
import { ResponderAttribution } from '@/components/ui/responder-attribution'
import { cn } from '@/lib/utils'

import { useMetaProposeState, type MetaProposeMessage } from './useMetaProposeState'

const INPUT_LABEL = 'Work request'
const INPUT_PLACEHOLDER = 'Describe work for the organisation...'

interface ProposeBubbleProps {
  msg: MetaProposeMessage
}

function ProposeBubble({ msg }: ProposeBubbleProps) {
  const isAttributed = Boolean(msg.responderRole && msg.responderName)
  return (
    <div
      className={cn(
        'rounded-md p-card text-sm text-foreground',
        msg.role === 'user' ? 'ml-8 bg-accent/10' : 'mr-8 bg-card',
      )}
    >
      <p className="whitespace-pre-wrap">{msg.content}</p>
      {msg.proposals && msg.proposals.length > 0 && (
        <ul className="mt-1 list-disc pl-4 text-xs text-text-secondary">
          {msg.proposals.map((title) => (
            <li key={title}>{title}</li>
          ))}
        </ul>
      )}
      {isAttributed && (
        <ResponderAttribution
          name={msg.responderName ?? ''}
          role={msg.responderRole ?? ''}
          topic={msg.routedTopic}
        />
      )}
    </div>
  )
}

export function MetaPropose() {
  const ctrl = useMetaProposeState()

  if (ctrl.messages.length === 0 && !ctrl.proposeLoading) {
    return (
      <div className="space-y-section-gap">
        <EmptyState
          icon={ClipboardList}
          title="Request work"
          description="Describe work in natural language. The Chief of Staff clarifies, then queues concrete items for your approval; concern-routed turns are answered by the matching role agent."
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
          <ProposeBubble key={msg.id} msg={msg} />
        ))}
        {ctrl.proposeLoading && (
          <div className="mr-8 animate-pulse rounded-md bg-card p-card text-sm text-muted-foreground">
            Working on it...
          </div>
        )}
      </div>

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
