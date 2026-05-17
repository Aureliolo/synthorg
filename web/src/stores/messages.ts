import { create } from 'zustand'
import * as messagesApi from '@/api/endpoints/messages'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
// Import sanitizers from the dedicated utility module rather than
// the notifications store -- avoids cross-store coupling and any
// circular-init risk.
import { sanitizeWsEnum, sanitizeWsString } from '@/utils/ws-sanitize'
import {
  MESSAGE_PRIORITY_VALUES,
  MESSAGE_TYPE_VALUES,
} from '@/api/types/messages'
import type { Channel, Message } from '@/api/types/messages'
import type { WsEvent } from '@/api/types/websocket'

const log = createLogger('messages')

const MESSAGES_FETCH_LIMIT = 50

type WireMessagePart = Message['parts'][number]

/**
 * Each ``parts`` / ``attachments`` entry must be a well-formed
 * structured part (``TextPart`` / ``DataPart`` / ``FilePart`` /
 * ``UriPart``). Without this the per-type sanitizer would dereference
 * a missing ``text`` / ``uri`` / ``data`` and throw. Unknown
 * discriminators are rejected outright (no safe fallback part type).
 */
function isPartsShape(value: unknown): boolean {
  if (!Array.isArray(value)) return false
  return value.every((part) => {
    if (typeof part !== 'object' || part === null || Array.isArray(part)) {
      return false
    }
    const p = part as Record<string, unknown>
    switch (p.type) {
      case 'text':
        return typeof p.text === 'string'
      case 'data':
        return (
          typeof p.data === 'object' &&
          p.data !== null &&
          !Array.isArray(p.data)
        )
      case 'file':
        return (
          typeof p.uri === 'string' &&
          (p.mime_type === null || typeof p.mime_type === 'string')
        )
      case 'uri':
        return typeof p.uri === 'string'
      default:
        return false
    }
  })
}

/**
 * Sanitize one structured part by discriminator. Every untrusted
 * string (``text`` / ``uri`` / ``mime_type``) is clamped via
 * ``sanitizeWsString``; ``DataPart.data`` is structured JSON kept
 * verbatim (it is rendered as data, never interpolated).
 */
function sanitizePart(part: Record<string, unknown>): WireMessagePart {
  switch (part.type) {
    case 'text':
      return {
        type: 'text',
        text: sanitizeWsString(part.text as string, 4096) ?? '',
      }
    case 'file':
      return {
        type: 'file',
        uri: sanitizeWsString(part.uri as string, 2048) ?? '',
        mime_type:
          part.mime_type === null
            ? null
            : sanitizeWsString(part.mime_type as string, 128) ?? '',
      }
    case 'uri':
      return {
        type: 'uri',
        uri: sanitizeWsString(part.uri as string, 2048) ?? '',
      }
    default:
      // ``data``: only reachable post-isPartsShape, so type is 'data'.
      return {
        type: 'data',
        data: part.data as Readonly<Record<string, unknown>>,
      }
  }
}

/**
 * ``MessageMetadata`` carries nullable id pointers, numeric usage
 * fields, and an ``extra`` array of ``[string, string]`` tuples. The
 * sanitizer dereferences every one of those, so we need to validate
 * their types here -- a missing/mistyped ``extra`` would otherwise
 * throw inside ``metadata.extra.map`` during sanitization.
 */
function isMessageMetadataShape(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const m = value as Record<string, unknown>
  if (m.task_id !== null && typeof m.task_id !== 'string') return false
  if (m.project_id !== null && typeof m.project_id !== 'string') return false
  // ``Number.isFinite`` rejects ``NaN``/``Infinity``/``-Infinity``. A bare
  // ``typeof === 'number'`` would let those through and poison the store
  // (downstream cost-aggregation / token-sum math silently propagates them).
  if (m.tokens_used !== null && !Number.isFinite(m.tokens_used)) return false
  if (m.cost !== null && !Number.isFinite(m.cost)) return false
  if (!Array.isArray(m.extra)) return false
  return m.extra.every(
    (entry) =>
      Array.isArray(entry) &&
      entry.length === 2 &&
      typeof entry[0] === 'string' &&
      typeof entry[1] === 'string',
  )
}

/**
 * Shallow structural check: every ``Message`` string field is a
 * ``string`` on the wire and ``attachments`` / ``metadata`` carry
 * well-formed nested shapes. Actual sanitization + non-empty
 * enforcement happens in ``parseWsMessage`` -- this guard only
 * rejects payloads whose fields are the wrong *type* before we
 * attempt to sanitize.
 */
function isMessageShape(
  c: Record<string, unknown>,
): c is Record<string, unknown> & Message {
  return (
    typeof c.id === 'string' &&
    typeof c.timestamp === 'string' &&
    typeof c.sender === 'string' &&
    typeof c.to === 'string' &&
    typeof c.channel === 'string' &&
    typeof c.text === 'string' &&
    typeof c.type === 'string' &&
    typeof c.priority === 'string' &&
    isPartsShape(c.parts) &&
    isPartsShape(c.attachments) &&
    isMessageMetadataShape(c.metadata)
  )
}

/**
 * Validate a WS payload and return a typed Message with every
 * untrusted string field sanitized, or null if malformed. All string
 * fields (``id``, ``timestamp``, ``sender``, ``to``, ``channel``,
 * ``content``, ``type``, ``priority``) go through ``sanitizeWsString``
 * to strip control chars and bidi-overrides and cap length. A required
 * string that sanitizes to empty causes the whole payload to be
 * rejected -- a message with no stable id or with a blank channel
 * cannot be displayed correctly, so there is no safe fallback.
 */
function parseWsMessage(
  payload: WsEvent['payload'],
): Message | null {
  if (
    !payload.message ||
    typeof payload.message !== 'object' ||
    Array.isArray(payload.message)
  ) return null

  const c = payload.message as Record<string, unknown>
  if (!isMessageShape(c)) {
    log.error(
      'Malformed WS payload, skipping',
      {
        id: sanitizeForLog(c.id),
        hasSender: typeof c.sender === 'string',
        hasChannel: typeof c.channel === 'string',
      },
    )
    return null
  }

  const id = sanitizeWsString(c.id, 128) ?? ''
  const timestamp = sanitizeWsString(c.timestamp, 64) ?? ''
  const sender = sanitizeWsString(c.sender) ?? ''
  const to = sanitizeWsString(c.to) ?? ''
  const channel = sanitizeWsString(c.channel) ?? ''
  const text = sanitizeWsString(c.text, 4096) ?? ''
  // Route enum-typed fields through sanitizeWsEnum so an unknown
  // backend value falls back to a safe default + emits a structured
  // ws.enum.unknown warning instead of being rendered verbatim. The
  // raw cast was the documented anti-pattern this PR replaces.
  const type = sanitizeWsEnum(c.type, MESSAGE_TYPE_VALUES, 'announcement', {
    maxLen: 64,
    field: 'message.type',
  })
  const priority = sanitizeWsEnum(c.priority, MESSAGE_PRIORITY_VALUES, 'normal', {
    maxLen: 64,
    field: 'message.priority',
  })

  if (!id || !timestamp || !sender || !to || !channel) {
    log.error('WS message blanked by sanitization, skipping', {
      id: sanitizeForLog(c.id),
      hasBlankId: id.length === 0,
      hasBlankTo: to.length === 0,
      hasBlankChannel: channel.length === 0,
    })
    return null
  }

  // Sanitize the structured parts (text/uri/mime strings clamped per
  // ``sanitizePart``; DataPart JSON kept verbatim). ``isPartsShape``
  // already validated both arrays, so the casts are sound.
  const parts = (c.parts as unknown as Record<string, unknown>[]).map(
    sanitizePart,
  )
  const attachments = (
    c.attachments as unknown as Record<string, unknown>[]
  ).map(sanitizePart)
  const metadata = {
    task_id:
      c.metadata.task_id === null
        ? null
        : sanitizeWsString(c.metadata.task_id, 128) ?? '',
    project_id:
      c.metadata.project_id === null
        ? null
        : sanitizeWsString(c.metadata.project_id, 128) ?? '',
    tokens_used: c.metadata.tokens_used,
    cost: c.metadata.cost,
    extra: c.metadata.extra.map(
      ([k, v]) =>
        [
          sanitizeWsString(k, 64) ?? '',
          sanitizeWsString(v, 512) ?? '',
        ] as [string, string],
    ),
  }

  // Build the returned ``Message`` explicitly -- a ``...c`` spread
  // would carry any unsanitized string keys present on the wire
  // payload (attacker-controlled enumerable props) straight into
  // store state, defeating the purpose of this function.
  return {
    id,
    timestamp,
    sender,
    to,
    channel,
    text,
    type,
    priority,
    parts,
    attachments,
    metadata,
  }
}

interface MessagesState {
  // Channels
  channels: Channel[]
  channelsLoading: boolean
  channelsError: string | null
  /**
   * Channel names that we have direct evidence carry at least one
   * message.  Populated by ``fetchChannelActivity`` (single-page scan
   * of recent messages without a channel filter), and incrementally
   * extended whenever a message arrives via WS.  The sidebar uses
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
  handleWsEvent: (event: WsEvent, activeChannel: string | null) => void
  toggleThread: (taskId: string) => void
  resetUnread: (channel: string) => void
  clearNewMessageIds: () => void
}

let channelRequestSeq = 0
let messageRequestSeq = 0
let activityRequestSeq = 0

/** Reset module-level sequence counters -- test-only. */
export function _resetRequestSeqs(): void {
  channelRequestSeq = 0
  messageRequestSeq = 0
  activityRequestSeq = 0
}

/**
 * Page size for the channel-activity probe.  Large enough that a
 * lightly-used deployment will see every active channel in one
 * request; small enough that the cost is bounded.  Channels with
 * NO messages in this window appear in the sidebar's "Empty" group
 * (collapsed by default).
 */
const CHANNEL_ACTIVITY_LIMIT = 200

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
  error: null,

  unreadCounts: {},
  expandedThreads: new Set<string>(),
  newMessageIds: new Set<string>(),

  fetchChannels: async () => {
    const seq = ++channelRequestSeq
    set({ channelsLoading: true, channelsError: null })
    try {
      // ``listChannels`` now returns a paginated envelope; take the
      // first page (channels are bounded by bus configuration, so a
      // single page at the default limit covers every deployment we
      // ship today).
      const result = await messagesApi.listChannels()
      if (seq !== channelRequestSeq) return
      set({ channels: result.data })
    } catch (err) {
      if (seq !== channelRequestSeq) return
      set({ channelsError: getErrorMessage(err) })
    } finally {
      if (seq === channelRequestSeq) set({ channelsLoading: false })
    }
  },

  fetchChannelActivity: async () => {
    const seq = ++activityRequestSeq
    try {
      // Single-page recent-messages probe without a channel filter --
      // every channel whose name appears here demonstrably carries at
      // least one recent message and lives in the "Active" sidebar
      // group.  Channels missing from this set fall through to the
      // collapsed "Empty" group so a fresh install's pre-created
      // topics don't bury active threads.
      const result = await messagesApi.listMessages({ limit: CHANNEL_ACTIVITY_LIMIT })
      if (seq !== activityRequestSeq) return
      // Merge into the existing set rather than overwriting it:
      // ``handleWsEvent`` adds channels live as messages arrive, and
      // a replace-on-completion would clobber any channel that became
      // active AFTER the activity probe was issued but BEFORE it
      // resolved (the probe's REST snapshot lags those WS events).
      const merged = new Set<string>(get().channelsWithMessages)
      for (const msg of result.data) merged.add(msg.channel)
      set({ channelsWithMessages: merged })
    } catch (err) {
      if (seq !== activityRequestSeq) return
      // The activity probe is a best-effort enhancement; on failure
      // we leave the previous classification in place so the sidebar
      // doesn't regress to a single-section list.
      log.warn('fetchChannelActivity failed', sanitizeForLog(err))
    }
  },

  fetchMessages: async (channel, limit = MESSAGES_FETCH_LIMIT) => {
    const seq = ++messageRequestSeq
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
      if (seq !== messageRequestSeq) return
      set({
        messages: result.data,
        total: result.data.length,
        nextCursor: result.nextCursor,
        hasMore: result.hasMore,
        loading: false,
        newMessageIds: new Set<string>(),
      })
    } catch (err) {
      if (seq !== messageRequestSeq) return
      set({
        loading: false,
        error: getErrorMessage(err),
        nextCursor: null,
        hasMore: false,
      })
    }
  },

  fetchMoreMessages: async (channel) => {
    const { loadingMore, nextCursor, hasMore } = get()
    if (loadingMore || !hasMore || !nextCursor) return
    const seq = messageRequestSeq
    set({ loadingMore: true, error: null })
    try {
      const result = await messagesApi.listMessages({
        channel,
        limit: MESSAGES_FETCH_LIMIT,
        cursor: nextCursor,
      })
      if (seq !== messageRequestSeq) return
      set((s) => {
        const existingIds = new Set(
          s.messages.map((m) => m.id),
        )
        const deduped = result.data.filter(
          (m) => !existingIds.has(m.id),
        )
        const mergedLength = s.messages.length + deduped.length
        return {
          messages: [...s.messages, ...deduped],
          total: mergedLength,
          nextCursor: result.nextCursor,
          hasMore: result.hasMore,
          loadingMore: false,
        }
      })
    } catch (err) {
      if (seq !== messageRequestSeq) return
      set({ loadingMore: false, error: getErrorMessage(err) })
    }
  },

  handleWsEvent: (event, activeChannel) => {
    const message = parseWsMessage(event.payload)
    if (!message) return

    // A live message proves the channel carries at least one entry,
    // so it graduates from the sidebar's "Empty" group to "Active"
    // immediately rather than waiting for the next activity probe.
    set((s) => {
      if (s.channelsWithMessages.has(message.channel)) return s
      const next = new Set(s.channelsWithMessages)
      next.add(message.channel)
      return { channelsWithMessages: next }
    })

    if (message.channel === activeChannel) {
      // Prepend to active channel (with dedup)
      set((s) => {
        if (s.messages.some((m) => m.id === message.id)) {
          return s
        }
        return {
          messages: [message, ...s.messages],
          total: s.total + 1,
          newMessageIds: new Set([
            ...s.newMessageIds,
            message.id,
          ]),
        }
      })
    } else {
      // Increment unread count for inactive channel
      set((s) => ({
        unreadCounts: {
          ...s.unreadCounts,
          [message.channel]: (s.unreadCounts[message.channel] ?? 0) + 1,
        },
      }))
    }
  },

  toggleThread: (taskId) => {
    set((s) => {
      const next = new Set(s.expandedThreads)
      if (next.has(taskId)) {
        next.delete(taskId)
      } else {
        next.add(taskId)
      }
      return { expandedThreads: next }
    })
  },

  resetUnread: (channel) => {
    set((s) => {
      if (!s.unreadCounts[channel]) return s
      const next = { ...s.unreadCounts }
      delete next[channel]
      return { unreadCounts: next }
    })
  },

  clearNewMessageIds: () => {
    set({ newMessageIds: new Set<string>() })
  },
}))
