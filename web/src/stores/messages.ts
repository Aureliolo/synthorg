import { create } from 'zustand'
import { _resetRequestSeqs } from './messages/_state'
import { createCrudActions } from './messages/crud-actions'
import { createWsHandler } from './messages/ws-handler'
import type { MessagesState } from './messages/types'

export type { MessagesState } from './messages/types'
export { _resetRequestSeqs }

export const useMessagesStore = create<MessagesState>()((set, get) => ({
  channels: [],
  channelsLoading: false,
  channelsError: null,
  channelsWithMessages: new Set<string>(),

  messages: [],
  total: 0,
  nextCursor: null,
  hasMore: false,
  loading: false,
  loadingMore: false,
  deleting: false,
  error: null,

  unreadCounts: {},
  expandedThreads: new Set<string>(),
  newMessageIds: new Set<string>(),

  ...createCrudActions(set, get),
  ...createWsHandler(set),
}))
