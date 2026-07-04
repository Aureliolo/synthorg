import { MessageCircle } from 'lucide-react'

import { ChatInputArea } from '@/components/ui/chat-input-area'
import { EmptyState } from '@/components/ui/empty-state'
import { ExamplePrompts } from '@/components/ui/example-prompts'
import { cn } from '@/lib/utils'

import { ChatErrorNotice } from './ChatErrorNotice'
import { ChatScopePicker } from './ChatScopePicker'
import {
  useChiefOfStaffChatState,
  type ChiefOfStaffMessage,
} from './useChiefOfStaffChatState'

const EXAMPLE_PROMPTS: readonly string[] = [
  'What is the organisation working on right now?',
  'Which improvement proposals are pending my review?',
  'Why did spend change over the last week?',
]

interface MessageBubbleProps {
  msg: ChiefOfStaffMessage
  onRetry: () => void
}

function MessageBubble({ msg, onRetry }: MessageBubbleProps) {
  if (msg.isError === true) {
    return (
      <div className="mr-8">
        <ChatErrorNotice message={msg.content} onRetry={onRetry} />
      </div>
    )
  }
  return (
    <div
      className={cn(
        'rounded-md p-card text-sm text-foreground',
        msg.role === 'user' ? 'ml-8 bg-accent/10' : 'mr-8 bg-card',
      )}
    >
      <p className="whitespace-pre-wrap">{msg.content}</p>
      {msg.sources && msg.sources.length > 0 && (
        <p className="mt-1 text-xs text-muted-foreground">
          Sources: {msg.sources.join(', ')}
        </p>
      )}
    </div>
  )
}

export function ChiefOfStaffChat() {
  const ctrl = useChiefOfStaffChatState()

  const scopePicker = (
    <ChatScopePicker
      proposals={ctrl.scopeableProposals}
      alerts={ctrl.scopeableAlerts}
      value={ctrl.scope}
      onChange={ctrl.setScope}
      disabled={ctrl.chatLoading}
    />
  )

  if (ctrl.messages.length === 0 && !ctrl.chatLoading) {
    return (
      <div className="mx-auto max-w-3xl space-y-section-gap">
        <EmptyState
          icon={MessageCircle}
          title="Ask the Chief of Staff"
          description="Ask questions about signals, proposals, or the improvement pipeline."
        />
        <ExamplePrompts
          prompts={EXAMPLE_PROMPTS}
          onSelect={ctrl.setInput}
          disabled={ctrl.chatLoading}
        />
        {scopePicker}
        <ChatInputArea
          value={ctrl.input}
          onChange={ctrl.setInput}
          onSend={ctrl.triggerSend}
          disabled={ctrl.chatLoading}
          label="Chat message"
          placeholder="Ask about signals, proposals..."
        />
      </div>
    )
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-section-gap">
      <div
        ref={ctrl.scrollRef}
        role="log"
        aria-label="Chief of Staff conversation"
        className="max-h-80 space-y-3 overflow-y-auto rounded-md border border-border p-card"
      >
        {ctrl.messages.map((msg) => (
          <MessageBubble key={msg.id} msg={msg} onRetry={() => ctrl.retryLast(msg.id)} />
        ))}
        {ctrl.chatLoading && (
          <div className="mr-8 animate-pulse rounded-md bg-card p-card text-sm text-muted-foreground">
            Thinking...
          </div>
        )}
      </div>

      {scopePicker}
      <ChatInputArea
        value={ctrl.input}
        onChange={ctrl.setInput}
        onSend={ctrl.triggerSend}
        disabled={ctrl.chatLoading}
        label="Chat message"
        placeholder="Ask about signals, proposals..."
      />
    </div>
  )
}
