import { MessageCircle } from 'lucide-react'

import { ChatInputArea } from '@/components/ui/chat-input-area'
import { EmptyState } from '@/components/ui/empty-state'
import { cn } from '@/lib/utils'

import { useMetaChatState, type MetaChatMessage } from './useMetaChatState'

interface MessageBubbleProps {
  msg: MetaChatMessage
}

function MessageBubble({ msg }: MessageBubbleProps) {
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

export function MetaChat() {
  const ctrl = useMetaChatState()

  if (ctrl.messages.length === 0 && !ctrl.chatLoading) {
    return (
      <div className="space-y-section-gap">
        <EmptyState
          icon={MessageCircle}
          title="Ask the Chief of Staff"
          description="Ask questions about signals, proposals, or the improvement pipeline."
        />
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
    <div className="flex flex-col gap-section-gap">
      <div
        ref={ctrl.scrollRef}
        role="log"
        aria-label="Chief of Staff conversation"
        className="max-h-80 space-y-3 overflow-y-auto rounded-md border border-border p-card"
      >
        {ctrl.messages.map((msg) => (
          <MessageBubble key={msg.id} msg={msg} />
        ))}
        {ctrl.chatLoading && (
          <div className="mr-8 animate-pulse rounded-md bg-card p-card text-sm text-muted-foreground">
            Thinking...
          </div>
        )}
      </div>

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
