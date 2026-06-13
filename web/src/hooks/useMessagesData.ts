import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useMessagesStore } from '@/stores/messages'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import type { Channel, Message } from '@/api/types/messages'
import type { WsChannel } from '@/api/types/websocket'

const MESSAGES_POLL_INTERVAL = 30_000
const MESSAGES_WS_CHANNELS = ['messages'] as const satisfies readonly WsChannel[]

export interface UseMessagesDataReturn {
  // Channels
  channels: Channel[]
  channelsLoading: boolean
  channelsError: string | null
  unreadCounts: Record<string, number>
  /** Channels with at least one observed message; drives the sidebar
   *  split between an "Active" group and a collapsed "Empty" group. */
  channelsWithMessages: ReadonlySet<string>

  // Messages
  messages: Message[]
  total: number
  loading: boolean
  loadingMore: boolean
  error: string | null
  hasMore: boolean

  // Thread state
  expandedThreads: Set<string>
  toggleThread: (taskId: string) => void

  // New-message flash tracking
  newMessageIds: Set<string>

  // Pagination
  fetchMore: () => void

  // Connection
  wsConnected: boolean
  wsSetupError: string | null
}

export function useMessagesData(activeChannel: string | null): UseMessagesDataReturn {
  // Ref avoids stale closure in WS handler (useWebSocket
  // registers bindings once, never re-registers)
  const activeChannelRef = useRef(activeChannel)
  useEffect(() => {
    activeChannelRef.current = activeChannel
  }, [activeChannel])

  const channels = useMessagesStore((s) => s.channels)
  const channelsLoading = useMessagesStore((s) => s.channelsLoading)
  const channelsError = useMessagesStore((s) => s.channelsError)
  const unreadCounts = useMessagesStore((s) => s.unreadCounts)
  const channelsWithMessages = useMessagesStore((s) => s.channelsWithMessages)

  const messages = useMessagesStore((s) => s.messages)
  const total = useMessagesStore((s) => s.total)
  const loading = useMessagesStore((s) => s.loading)
  const loadingMore = useMessagesStore((s) => s.loadingMore)
  const error = useMessagesStore((s) => s.error)

  const expandedThreads = useMessagesStore((s) => s.expandedThreads)
  const toggleThread = useMessagesStore((s) => s.toggleThread)
  const newMessageIds = useMessagesStore((s) => s.newMessageIds)

  // Fetch channels on mount
  useEffect(() => {
    void useMessagesStore.getState().fetchChannels()
    // Probe recent global messages once so the sidebar knows which
    // channels are non-empty without having to visit each one.
    void useMessagesStore.getState().fetchChannelActivity()
  }, [])

  // Fetch messages when active channel changes; reset unread
  useEffect(() => {
    if (!activeChannel) return
    void useMessagesStore.getState().fetchMessages(activeChannel)
    useMessagesStore.getState().resetUnread(activeChannel)
  }, [activeChannel])

  // Polling for current channel refresh, gated against fresh WS state.
  const { skipIfFresh, markFresh } = useFreshnessGate()
  const pollFn = useCallback(async () => {
    if (!activeChannel) return
    await useMessagesStore.getState().fetchMessages(activeChannel)
  }, [activeChannel])

  const polling = usePolling(pollFn, MESSAGES_POLL_INTERVAL, { skipIfFresh })

  const { start, stop } = polling
  useEffect(() => {
    if (!activeChannel) return
    start()
    return () => stop()
  }, [activeChannel, start, stop])

  // WebSocket bindings (stable -- reads activeChannel via ref)
  const bindings: ChannelBinding[] = useMemo(
    () =>
      MESSAGES_WS_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          // Only mark fresh when the event actually mutated the ACTIVE
          // channel; a frame for some other channel must not suppress the
          // active channel's poll, which would leave its thread stale.
          const didAffectActiveChannel = useMessagesStore
            .getState()
            .handleWsEvent(event, activeChannelRef.current)
          if (didAffectActiveChannel) {
            markFresh()
          }
        },
      })),
    [markFresh],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({ bindings })

  // Computed
  const hasMore = useMemo(() => messages.length < total, [messages.length, total])

  const fetchMore = useCallback(() => {
    if (!activeChannel) return
    void useMessagesStore.getState().fetchMoreMessages(activeChannel)
  }, [activeChannel])

  return {
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
  }
}
