import { useCallback, useEffect, useMemo, useRef } from 'react'
import { Loader2, MessageSquare } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import {
  groupMessagesByDate,
  groupMessagesByThread,
  getDateGroupLabel,
} from '@/utils/messages'
import { TimestampDivider } from './TimestampDivider'
import { MessageThread } from './MessageThread'
import { MessageBubble } from './MessageBubble'
import type { Message } from '@/api/types/messages'

interface MessageListProps {
  messages: readonly Message[]
  expandedThreads: ReadonlySet<string>
  toggleThread: (taskId: string) => void
  onSelectMessage: (id: string) => void
  hasMore: boolean
  loadingMore: boolean
  onLoadMore: () => void
  newMessageIds?: ReadonlySet<string>
}

export function MessageList({
  messages,
  expandedThreads,
  toggleThread,
  onSelectMessage,
  hasMore,
  loadingMore,
  onLoadMore,
  newMessageIds,
}: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const wasAtBottomRef = useRef(true)

  const handleScroll = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    wasAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }, [])

  useAutoScrollOnNewMessages(containerRef, wasAtBottomRef, messages.length)

  const sorted = useMemo(
    () => [...messages].sort((a, b) => a.timestamp.localeCompare(b.timestamp)),
    [messages],
  )
  const dateGroups = useMemo(() => groupMessagesByDate(sorted), [sorted])

  if (messages.length === 0) {
    return (
      <EmptyState
        icon={MessageSquare}
        title="No messages"
        description="No messages in this channel yet."
      />
    )
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto"
      aria-live="polite"
      aria-label="Messages"
    >
      {hasMore && (
        <LoadMoreButton loadingMore={loadingMore} onLoadMore={onLoadMore} />
      )}
      {[...dateGroups.entries()].map(([dateKey, msgs]) => (
        <DateGroupSection
          key={dateKey}
          dateKey={dateKey}
          messages={[...msgs]}
          expandedThreads={expandedThreads}
          toggleThread={toggleThread}
          onSelectMessage={onSelectMessage}
          newMessageIds={newMessageIds}
        />
      ))}
    </div>
  )
}

function useAutoScrollOnNewMessages(
  containerRef: React.RefObject<HTMLDivElement | null>,
  wasAtBottomRef: React.MutableRefObject<boolean>,
  messageCount: number,
): void {
  const prevLengthRef = useRef(messageCount)
  useEffect(() => {
    if (messageCount > prevLengthRef.current && wasAtBottomRef.current) {
      const el = containerRef.current
      if (el) el.scrollTop = el.scrollHeight
    }
    prevLengthRef.current = messageCount
  }, [messageCount, containerRef, wasAtBottomRef])
}

interface LoadMoreButtonProps {
  loadingMore: boolean
  onLoadMore: () => void
}

function LoadMoreButton({ loadingMore, onLoadMore }: LoadMoreButtonProps) {
  return (
    <div className="flex justify-center py-2">
      <Button variant="ghost" size="sm" onClick={onLoadMore} disabled={loadingMore}>
        {loadingMore && <Loader2 className="size-3 animate-spin" />}
        {loadingMore ? 'Loading...' : 'Load earlier messages'}
      </Button>
    </div>
  )
}

interface DateGroupSectionProps {
  dateKey: string
  messages: Message[]
  expandedThreads: ReadonlySet<string>
  toggleThread: (taskId: string) => void
  onSelectMessage: (id: string) => void
  newMessageIds: ReadonlySet<string> | undefined
}

function DateGroupSection({
  dateKey,
  messages,
  expandedThreads,
  toggleThread,
  onSelectMessage,
  newMessageIds,
}: DateGroupSectionProps) {
  const { threads, standalone } = groupMessagesByThread(messages)
  return (
    <div>
      <TimestampDivider label={getDateGroupLabel(dateKey)} />
      {[...threads.entries()].map(([taskId, threadMsgs]) => (
        <MessageThread
          key={taskId}
          messages={threadMsgs}
          expanded={expandedThreads.has(taskId)}
          onToggle={() => toggleThread(taskId)}
          onSelectMessage={onSelectMessage}
          newMessageIds={newMessageIds}
        />
      ))}
      {standalone.map((msg) => (
        <MessageBubble
          key={msg.id}
          message={msg}
          isNew={newMessageIds?.has(msg.id)}
          onClick={() => onSelectMessage(msg.id)}
        />
      ))}
    </div>
  )
}
