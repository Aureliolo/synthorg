import { AnimatePresence } from 'motion/react'
import { MessageSquare } from 'lucide-react'
import { ErrorBanner } from '@/components/ui/error-banner'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ChannelSidebar } from './messages/ChannelSidebar'
import { MessageFilterBar } from './messages/MessageFilterBar'
import { MessageList } from './messages/MessageList'
import { MessageDetailDrawer } from './messages/MessageDetailDrawer'
import { MessagesSkeleton } from './messages/MessagesSkeleton'
import {
  useMessagesPageController,
  type MessagesPageController,
} from './messages/useMessagesPageController'

export default function MessagesPage() {
  const ctrl = useMessagesPageController()
  if (ctrl.showInitialSkeleton) return <MessagesSkeleton />

  return (
    <div className="flex h-[calc(100vh-theme(spacing.16))] gap-section-gap">
      <ErrorBoundary level="section">
        <ChannelSidebar
          channels={ctrl.data.channels}
          activeChannel={ctrl.activeChannel}
          unreadCounts={ctrl.data.unreadCounts}
          channelsWithMessages={ctrl.data.channelsWithMessages}
          onSelectChannel={ctrl.handleSelectChannel}
          loading={ctrl.data.channelsLoading}
        />
      </ErrorBoundary>

      <MessagesMainContent ctrl={ctrl} />

      <AnimatePresence>
        {ctrl.selectedMessageId && (
          <MessageDetailDrawer
            message={ctrl.selectedMessage}
            open
            onClose={ctrl.handleCloseDrawer}
          />
        )}
      </AnimatePresence>
    </div>
  )
}

interface MessagesMainContentProps {
  ctrl: MessagesPageController
}

function MessagesMainContent({ ctrl }: MessagesMainContentProps) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-section-gap">
      <h1 className="text-lg font-semibold text-foreground">Messages</h1>
      <MessagesErrorBanners ctrl={ctrl} />
      {!ctrl.activeChannel && (
        <EmptyState
          icon={MessageSquare}
          title="Select a channel"
          description="Choose a channel from the sidebar to view messages."
        />
      )}
      {ctrl.activeChannel && <MessagesChannelView ctrl={ctrl} />}
    </div>
  )
}

interface MessagesErrorBannersProps {
  ctrl: MessagesPageController
}

function MessagesErrorBanners({ ctrl }: MessagesErrorBannersProps) {
  return (
    <>
      {ctrl.data.error && (
        <ErrorBanner
          severity="error"
          title="Could not load messages"
          description={ctrl.data.error}
        />
      )}
      {ctrl.data.channelsError && ctrl.data.channelsError !== ctrl.data.error && (
        <ErrorBanner
          severity="error"
          title="Could not load channels"
          description={ctrl.data.channelsError}
        />
      )}
      {ctrl.showOfflineBanner && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={
            ctrl.data.wsSetupError ?? 'Data may be stale until the connection recovers.'
          }
        />
      )}
    </>
  )
}

interface MessagesChannelViewProps {
  ctrl: MessagesPageController
}

function MessagesChannelView({ ctrl }: MessagesChannelViewProps) {
  return (
    <>
      <MessageFilterBar
        filters={ctrl.filters}
        onFiltersChange={ctrl.handleFiltersChange}
        totalCount={ctrl.data.total}
        filteredCount={ctrl.hasFilters ? ctrl.filtered.length : undefined}
      />
      <ErrorBoundary level="section">
        <MessageList
          messages={ctrl.filtered}
          expandedThreads={ctrl.data.expandedThreads}
          toggleThread={ctrl.data.toggleThread}
          onSelectMessage={ctrl.handleSelectMessage}
          hasMore={ctrl.data.hasMore && !ctrl.hasFilters}
          loadingMore={ctrl.data.loadingMore}
          onLoadMore={ctrl.data.fetchMore}
          newMessageIds={ctrl.data.newMessageIds}
        />
      </ErrorBoundary>
    </>
  )
}
