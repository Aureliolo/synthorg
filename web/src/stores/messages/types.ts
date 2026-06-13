import type { StoreApi } from 'zustand'
import type { Channel, Message } from '@/api/types/messages'
import type { WsEvent } from '@/api/types/websocket'

export interface MessagesState {
  // Channels
  channels: Channel[]
  channelsLoading: boolean
  channelsError: string | null
  /**
   * Channel names that we have direct evidence carry at least one
   * message. Populated by ``fetchChannelActivity`` (single-page scan
   * of recent messages without a channel filter), and incrementally
   * extended whenever a message arrives via WS. The sidebar uses
   * this set to split topics into an "Active" group and a collapsed
   * "Empty" group so a fresh install's long list of never-used
   * topics doesn't drown the surface.
   */
  channelsWithMessages: Set<string>

  // Messages (for active channel)
  messages: Message[]
  total: number
  /** Opaque cursor for the next page; null on the final page. */
  nextCursor: string | null
  /** Whether more messages follow the current page. */
  hasMore: boolean
  loading: boolean
  loadingMore: boolean
  error: string | null

  // Unread tracking: channel name -> count
  unreadCounts: Record<string, number>

  // Thread expansion: Set of task_id values
  expandedThreads: Set<string>

  // New-message flash tracking (WS-delivered IDs)
  newMessageIds: Set<string>

  // Actions
  fetchChannels: () => Promise<void>
  fetchChannelActivity: () => Promise<void>
  fetchMessages: (channel: string, limit?: number) => Promise<void>
  fetchMoreMessages: (channel: string) => Promise<void>
  /** Returns true when the event mutated the active channel's thread, so
   *  the caller can gate freshness on a real active-channel update. */
  handleWsEvent: (event: WsEvent, activeChannel: string | null) => boolean
  toggleThread: (taskId: string) => void
  resetUnread: (channel: string) => void
  clearNewMessageIds: () => void
}

export type MessagesSet = StoreApi<MessagesState>['setState']
export type MessagesGet = StoreApi<MessagesState>['getState']
