import { MessageCircle, Send } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { InputField } from '@/components/ui/input-field'
import { LiveRegion } from '@/components/ui/live-region'
import { cn } from '@/lib/utils'

import { useMetaChatState, type MetaChatMessage } from './useMetaChatState'

interface ChatInputAreaProps {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  disabled: boolean
  className?: string
}

function ChatInputArea({
  value,
  onChange,
  onSend,
  disabled,
  className,
}: ChatInputAreaProps) {
  return (
    <div className={cn('flex gap-2', className)}>
      <div className="flex-1">
        <InputField
          label="Chat message"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Ask about signals, proposals..."
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              onSend()
            }
          }}
        />
      </div>
      <Button
        size="sm"
        onClick={onSend}
        disabled={!value.trim() || disabled}
        aria-label="Send message"
      >
        <Send className="h-4 w-4" />
      </Button>
    </div>
  )
}

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
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div
        ref={ctrl.scrollRef}
        className="max-h-80 space-y-3 overflow-y-auto rounded-md border border-border p-card"
      >
        <LiveRegion politeness="polite">
          {ctrl.messages.map((msg) => (
            <MessageBubble key={msg.id} msg={msg} />
          ))}
          {ctrl.chatLoading && (
            <div className="mr-8 animate-pulse rounded-md bg-card p-card text-sm text-muted-foreground">
              Thinking...
            </div>
          )}
        </LiveRegion>
      </div>

      <ChatInputArea
        value={ctrl.input}
        onChange={ctrl.setInput}
        onSend={ctrl.triggerSend}
        disabled={ctrl.chatLoading}
      />
    </div>
  )
}
