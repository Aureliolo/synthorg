import { useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'
import { Avatar } from '@/components/ui/avatar'
import { useFlash } from '@/hooks/useFlash'
import { formatRelativeTime } from '@/utils/format'
import {
  getMessagePriorityColor,
  getPriorityDotClass,
  messageText,
  partsToAttachments,
} from '@/utils/messages'
import { MessageTypeBadge } from './MessageTypeBadge'
import { AttachmentList } from './AttachmentList'
import type { Message } from '@/api/types/messages'

interface MessageBubbleProps {
  message: Message
  isNew?: boolean
  onClick?: () => void
}

export function MessageBubble({ message, isNew, onClick }: MessageBubbleProps) {
  const { triggerFlash, flashStyle } = useFlash()
  const hasTriggeredRef = useRef(false)

  useEffect(() => {
    if (isNew && !hasTriggeredRef.current) {
      hasTriggeredRef.current = true
      triggerFlash()
    }
  }, [isNew, triggerFlash])

  const priorityColor = getMessagePriorityColor(message.priority)
  const relativeTime = formatRelativeTime(message.timestamp)
  // Truncate the content preview to keep the accessible name short
  // (long aria-labels overwhelm screen readers and lose context).
  // Collapse whitespace so multi-line / multi-space bodies don't
  // produce ragged labels.
  const contentPreview = messageText(message).trim().replace(/\s+/g, ' ').slice(0, 120)
  const attachments = partsToAttachments(message.parts)

  // ``priorityColor`` is null for ``normal`` priority (the default
  // visible state has no dot), so we only mention priority in the
  // accessible label when there's a visible priority indicator.
  // Otherwise the surrounding test contract (``does not render
  // priority indicator for normal priority``) breaks.
  const ariaLabel = [
    `${message.type} from ${message.sender}`,
    priorityColor ? `${message.priority} priority` : null,
    `sent ${relativeTime}`,
    contentPreview ? `message ${contentPreview}` : null,
  ]
    .filter(Boolean)
    .join(', ')

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      className={cn(
        'flex w-full gap-3 rounded-lg p-card text-left transition-colors',
        'hover:bg-card-hover',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
      )}
      style={flashStyle}
    >
      <Avatar name={message.sender} size="sm" />
      <div className="min-w-0 flex-1 space-y-1">
        {/* Header row */}
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-xs font-medium text-foreground">{message.sender}</span>
          <MessageTypeBadge type={message.type} />
          {priorityColor && (
            <span
              className={cn('size-1.5 rounded-full', getPriorityDotClass(priorityColor))}
              aria-label={`${message.priority} priority`}
            />
          )}
          <span className="ml-auto shrink-0 font-mono text-micro text-muted-foreground">
            {relativeTime}
          </span>
        </div>

        {/* Content */}
        <p className="whitespace-pre-wrap text-sm text-foreground">
          {messageText(message)}
        </p>

        {/* Attachments */}
        {attachments.length > 0 && (
          <div className="pt-1">
            <AttachmentList attachments={attachments} />
          </div>
        )}
      </div>
    </button>
  )
}
