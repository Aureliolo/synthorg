import type { Message } from '@/api/types/messages'
import type { WsEvent } from '@/api/types/websocket'
import { parseWsMessage } from './sanitize'
import type { MessagesSet } from './types'

function noteChannelActivity(set: MessagesSet, channel: string): void {
  set((s) => {
    if (s.channelsWithMessages.has(channel)) return s
    const next = new Set(s.channelsWithMessages)
    next.add(channel)
    return { channelsWithMessages: next }
  })
}

function appendToActiveChannel(set: MessagesSet, message: Message): void {
  set((s) => {
    if (s.messages.some((m) => m.id === message.id)) return s
    return {
      messages: [message, ...s.messages],
      total: s.total + 1,
      newMessageIds: new Set([...s.newMessageIds, message.id]),
    }
  })
}

function bumpUnreadCount(set: MessagesSet, channel: string): void {
  set((s) => ({
    unreadCounts: {
      ...s.unreadCounts,
      [channel]: (s.unreadCounts[channel] ?? 0) + 1,
    },
  }))
}

function handleWsEventImpl(
  set: MessagesSet,
  event: WsEvent,
  activeChannel: string | null,
): void {
  const message = parseWsMessage(event.payload)
  if (!message) return
  // A live message proves the channel carries at least one entry,
  // so it graduates from the sidebar's "Empty" group to "Active"
  // immediately rather than waiting for the next activity probe.
  noteChannelActivity(set, message.channel)
  if (message.channel === activeChannel) {
    appendToActiveChannel(set, message)
  } else {
    bumpUnreadCount(set, message.channel)
  }
}

export function createWsHandler(set: MessagesSet) {
  return {
    handleWsEvent: (event: WsEvent, activeChannel: string | null) =>
      handleWsEventImpl(set, event, activeChannel),
    toggleThread(taskId: string) {
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
    resetUnread(channel: string) {
      set((s) => {
        if (!s.unreadCounts[channel]) return s
        const next = { ...s.unreadCounts }
        Reflect.deleteProperty(next, channel)
        return { unreadCounts: next }
      })
    },
    clearNewMessageIds() {
      set({ newMessageIds: new Set<string>() })
    },
  }
}
