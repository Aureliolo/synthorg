import { AnimatePresence, motion } from 'motion/react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import { springGentle, tweenFast } from '@/lib/motion'
import { MessageBubble } from './MessageBubble'
import type { Message } from '@/api/types/messages'

interface MessageThreadProps {
  messages: readonly Message[]
  expanded: boolean
  onToggle: () => void
  onSelectMessage: (id: string) => void
  newMessageIds?: ReadonlySet<string> | undefined
}

export function MessageThread({
  messages,
  expanded,
  onToggle,
  onSelectMessage,
  newMessageIds,
}: MessageThreadProps) {
  // Single message -- render standalone, no thread UI
  if (messages.length <= 1) {
    const msg = messages[0]
    if (!msg) return null
    return (
      <MessageBubble
        message={msg}
        isNew={newMessageIds?.has(msg.id)}
        onClick={() => onSelectMessage(msg.id)}
      />
    )
  }

  const first = messages[0]!
  const remaining = messages.length - 1

  return (
    <div>
      <MessageBubble
        message={first}
        isNew={newMessageIds?.has(first.id)}
        onClick={() => onSelectMessage(first.id)}
      />

      {/* Thread expand/collapse pill */}
      <button
        type="button"
        onClick={onToggle}
        className={cn(
          'ml-9 flex items-center gap-1 rounded-full border border-border bg-surface px-2 py-0.5',
          'font-mono text-[10px] text-text-secondary transition-colors',
          'hover:bg-card-hover hover:text-foreground',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
        )}
        aria-expanded={expanded}
      >
        <ChevronDown
          className={cn('size-3 transition-transform', expanded && 'rotate-180')}
          aria-hidden="true"
        />
        {expanded ? 'Collapse thread' : `${remaining} more in thread`}
      </button>

      {/* Expanded thread messages */}
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1, transition: springGentle }}
            exit={{ height: 0, opacity: 0, transition: tweenFast }}
            className="overflow-hidden"
          >
            <div className="ml-5 border-l-2 border-accent/30 pl-3">
              {messages.slice(1).map((msg) => (
                <MessageBubble
                  key={msg.id}
                  message={msg}
                  isNew={newMessageIds?.has(msg.id)}
                  onClick={() => onSelectMessage(msg.id)}
                />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
