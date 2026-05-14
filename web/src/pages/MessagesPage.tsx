import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useSearchParams } from 'react-router'
import { AnimatePresence } from 'motion/react'
import { MessageSquare } from 'lucide-react'
import { ErrorBanner } from '@/components/ui/error-banner'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { useMessagesData } from '@/hooks/useMessagesData'
import { useMessagesStore } from '@/stores/messages'
import { filterMessages, type MessagePageFilters } from '@/utils/messages'
import { ChannelSidebar } from './messages/ChannelSidebar'
import { MessageFilterBar } from './messages/MessageFilterBar'
import { MessageList } from './messages/MessageList'
import { MessageDetailDrawer } from './messages/MessageDetailDrawer'
import { MessagesSkeleton } from './messages/MessagesSkeleton'
import type { MessagePriority, MessageType } from '@/api/types/messages'

const VALID_TYPES: ReadonlySet<string> = new Set([
  'task_update', 'question', 'announcement', 'review_request', 'approval',
  'delegation', 'status_report', 'escalation', 'meeting_contribution', 'hr_notification',
])
const VALID_PRIORITIES: ReadonlySet<string> = new Set(['low', 'normal', 'high', 'urgent'])

export default function MessagesPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const wasConnectedRef = useRef(false)

  const activeChannel = searchParams.get('channel')

  const {
    channels,
    channelsLoading,
    channelsError,
    unreadCounts,
    channelsWithMessages,
    messages,
    total,
    loading,
    loadingMore,
    error,
    hasMore,
    expandedThreads,
    toggleThread,
    newMessageIds,
    fetchMore,
    wsConnected,
    wsSetupError,
  } = useMessagesData(activeChannel)

  // Track WS connection to avoid flash on initial load
  if (wsConnected) wasConnectedRef.current = true

  // Auto-clear new-message flash IDs after animation
  useEffect(() => {
    if (newMessageIds.size === 0) return
    const timer = setTimeout(() => {
      useMessagesStore
        .getState()
        .clearNewMessageIds()
    }, 2000)
    return () => clearTimeout(timer)
  }, [newMessageIds])

  // URL-synced filters
  const filters: MessagePageFilters = useMemo(() => {
    const rawType = searchParams.get('type')
    const rawPriority = searchParams.get('priority')
    return {
      type: rawType && VALID_TYPES.has(rawType) ? rawType as MessageType : undefined,
      priority: rawPriority && VALID_PRIORITIES.has(rawPriority) ? rawPriority as MessagePriority : undefined,
      search: searchParams.get('search') ?? undefined,
    }
  }, [searchParams])

  const handleFiltersChange = useCallback((newFilters: MessagePageFilters) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('type')
      next.delete('priority')
      next.delete('search')
      if (newFilters.type) next.set('type', newFilters.type)
      if (newFilters.priority) next.set('priority', newFilters.priority)
      if (newFilters.search) next.set('search', newFilters.search)
      return next
    })
  }, [setSearchParams])

  const handleSelectChannel = useCallback((name: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('channel', name)
      next.delete('message')
      next.delete('type')
      next.delete('priority')
      next.delete('search')
      return next
    })
  }, [setSearchParams])

  const selectedMessageId = searchParams.get('message')
  const selectedMessage = useMemo(
    () => messages.find((m) => m.id === selectedMessageId) ?? null,
    [messages, selectedMessageId],
  )

  const handleSelectMessage = useCallback((id: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('message', id)
      return next
    })
  }, [setSearchParams])

  const handleCloseDrawer = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('message')
      return next
    })
  }, [setSearchParams])

  // Client-side filtering
  const filtered = useMemo(() => filterMessages(messages, filters), [messages, filters])
  const hasFilters = !!(filters.type || filters.priority || filters.search)

  // Loading state
  if (loading && messages.length === 0 && channelsLoading && channels.length === 0) {
    return <MessagesSkeleton />
  }

  return (
    <div className="flex h-[calc(100vh-theme(spacing.16))] gap-6">
      {/* Channel sidebar */}
      <ErrorBoundary level="section">
        <ChannelSidebar
          channels={channels}
          activeChannel={activeChannel}
          unreadCounts={unreadCounts}
          channelsWithMessages={channelsWithMessages}
          onSelectChannel={handleSelectChannel}
          loading={channelsLoading}
        />
      </ErrorBoundary>

      {/* Main content area */}
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <h1 className="text-lg font-semibold text-foreground">Messages</h1>

        {/* Error banners */}
        {error && (
          <ErrorBanner severity="error" title="Could not load messages" description={error} />
        )}
        {channelsError && channelsError !== error && (
          <ErrorBanner severity="error" title="Could not load channels" description={channelsError} />
        )}

        {(wsSetupError || (wasConnectedRef.current && !wsConnected)) && !loading && (
          <ErrorBanner
            variant="offline"
            title="Real-time updates disconnected"
            description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
          />
        )}

        {/* No channel selected prompt */}
        {!activeChannel && (
          <EmptyState
            icon={MessageSquare}
            title="Select a channel"
            description="Choose a channel from the sidebar to view messages."
          />
        )}

        {/* Channel selected -- show filter bar and message list */}
        {activeChannel && (
          <>
            <MessageFilterBar
              filters={filters}
              onFiltersChange={handleFiltersChange}
              totalCount={total}
              filteredCount={hasFilters ? filtered.length : undefined}
            />

            <ErrorBoundary level="section">
              <MessageList
                messages={filtered}
                expandedThreads={expandedThreads}
                toggleThread={toggleThread}
                onSelectMessage={handleSelectMessage}
                hasMore={hasMore && !hasFilters}
                loadingMore={loadingMore}
                onLoadMore={fetchMore}
                newMessageIds={newMessageIds}
              />
            </ErrorBoundary>
          </>
        )}
      </div>

      {/* Detail drawer */}
      <AnimatePresence>
        {selectedMessageId && (
          <MessageDetailDrawer
            message={selectedMessage}
            open={!!selectedMessageId}
            onClose={handleCloseDrawer}
          />
        )}
      </AnimatePresence>
    </div>
  )
}
