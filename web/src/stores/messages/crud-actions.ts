import * as messagesApi from '@/api/endpoints/messages'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import {
  getActivityRequestSeq,
  getChannelRequestSeq,
  getMessageRequestSeq,
  nextActivityRequestSeq,
  nextChannelRequestSeq,
  nextMessageRequestSeq,
} from './_state'
import type { MessagesGet, MessagesSet } from './types'

const log = createLogger('messages')

const MESSAGES_FETCH_LIMIT = 50

/**
 * Page size for the channel-activity probe. Large enough that a
 * lightly-used deployment will see every active channel in one
 * request; small enough that the cost is bounded.
 */
const CHANNEL_ACTIVITY_LIMIT = 200

async function fetchChannelsImpl(set: MessagesSet): Promise<void> {
  const seq = nextChannelRequestSeq()
  set({ channelsLoading: true, channelsError: null })
  try {
    const result = await messagesApi.listChannels()
    if (seq !== getChannelRequestSeq()) return
    set({ channels: result.data })
  } catch (err) {
    if (seq !== getChannelRequestSeq()) return
    set({ channelsError: getErrorMessage(err) })
  } finally {
    if (seq === getChannelRequestSeq()) set({ channelsLoading: false })
  }
}

async function fetchChannelActivityImpl(
  set: MessagesSet,
  get: MessagesGet,
): Promise<void> {
  const seq = nextActivityRequestSeq()
  try {
    const result = await messagesApi.listMessages({
      limit: CHANNEL_ACTIVITY_LIMIT,
    })
    if (seq !== getActivityRequestSeq()) return
    // Merge into the existing set rather than overwriting it:
    // ``handleWsEvent`` adds channels live as messages arrive, and
    // a replace-on-completion would clobber any channel that became
    // active AFTER the activity probe was issued but BEFORE it
    // resolved (the probe's REST snapshot lags those WS events).
    const merged = new Set<string>(get().channelsWithMessages)
    for (const msg of result.data) merged.add(msg.channel)
    set({ channelsWithMessages: merged })
  } catch (err) {
    if (seq !== getActivityRequestSeq()) return
    // The activity probe is a best-effort enhancement; on failure
    // we leave the previous classification in place so the sidebar
    // doesn't regress to a single-section list.
    log.warn('fetchChannelActivity failed', sanitizeForLog(err))
  }
}

async function fetchMessagesImpl(
  set: MessagesSet,
  channel: string,
  limit = MESSAGES_FETCH_LIMIT,
): Promise<void> {
  const seq = nextMessageRequestSeq()
  // Clear stale cursor state so fetchMoreMessages cannot resume from
  // a cursor issued for a previous channel if this fresh load fails.
  set({
    loading: true,
    error: null,
    loadingMore: false,
    nextCursor: null,
    hasMore: false,
  })
  try {
    const result = await messagesApi.listMessages({ channel, limit })
    if (seq !== getMessageRequestSeq()) return
    set({
      messages: result.data,
      total: result.data.length,
      nextCursor: result.nextCursor,
      hasMore: result.hasMore,
      loading: false,
      newMessageIds: new Set<string>(),
    })
  } catch (err) {
    if (seq !== getMessageRequestSeq()) return
    set({
      loading: false,
      error: getErrorMessage(err),
      nextCursor: null,
      hasMore: false,
    })
  }
}

async function fetchMoreMessagesImpl(
  set: MessagesSet,
  get: MessagesGet,
  channel: string,
): Promise<void> {
  const { loadingMore, nextCursor, hasMore } = get()
  if (loadingMore || !hasMore || !nextCursor) return
  const seq = getMessageRequestSeq()
  set({ loadingMore: true, error: null })
  try {
    const result = await messagesApi.listMessages({
      channel,
      limit: MESSAGES_FETCH_LIMIT,
      cursor: nextCursor,
    })
    if (seq !== getMessageRequestSeq()) return
    set((s) => {
      const existingIds = new Set(s.messages.map((m) => m.id))
      const deduped = result.data.filter((m) => !existingIds.has(m.id))
      return {
        messages: [...s.messages, ...deduped],
        total: s.messages.length + deduped.length,
        nextCursor: result.nextCursor,
        hasMore: result.hasMore,
        loadingMore: false,
      }
    })
  } catch (err) {
    if (seq !== getMessageRequestSeq()) return
    set({ loadingMore: false, error: getErrorMessage(err) })
  }
}

export function createCrudActions(set: MessagesSet, get: MessagesGet) {
  return {
    fetchChannels: () => fetchChannelsImpl(set),
    fetchChannelActivity: () => fetchChannelActivityImpl(set, get),
    fetchMessages: (channel: string, limit?: number) =>
      fetchMessagesImpl(set, channel, limit),
    fetchMoreMessages: (channel: string) =>
      fetchMoreMessagesImpl(set, get, channel),
  }
}
