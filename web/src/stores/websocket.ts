import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { WsChannel, WsEvent, WsEventHandler } from '@/api/types'
import { WS_RECONNECT_BASE_DELAY, WS_RECONNECT_MAX_DELAY, WS_MAX_RECONNECT_ATTEMPTS, WS_MAX_MESSAGE_SIZE } from '@/utils/constants'
import { sanitizeForLog } from '@/utils/logging'

/** Build a stable deduplication key for a subscription (sorted channels + sorted filter keys). */
function subscriptionKey(channels: WsChannel[], filters?: Record<string, string>): string {
  const sortedChannels = [...channels].sort()
  const sortedFilters: Record<string, string> = {}
  if (filters) {
    for (const key of Object.keys(filters).sort()) {
      sortedFilters[key] = filters[key]
    }
  }
  return JSON.stringify({ channels: sortedChannels, filters: sortedFilters })
}

export const useWebSocketStore = defineStore('websocket', () => {
  const connected = ref(false)
  const reconnectExhausted = ref(false)
  const subscribedChannels = ref<WsChannel[]>([])

  let socket: WebSocket | null = null
  let reconnectAttempts = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let intentionalClose = false
  let currentToken: string | null = null
  const channelHandlers = new Map<string, Set<WsEventHandler>>()
  let pendingSubscriptions: { channels: WsChannel[]; filters?: Record<string, string> }[] = []
  // Track active subscriptions so reconnect can re-subscribe automatically
  const activeSubscriptions: { channels: WsChannel[]; filters?: Record<string, string> }[] = []

  function getWsUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    return `${protocol}//${host}/api/v1/ws`
  }

  function connect(token: string) {
    if (socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return
    reconnectExhausted.value = false

    currentToken = token
    intentionalClose = false
    // TODO(#343): Replace with one-time WS ticket endpoint for production security.
    // Currently passes JWT as query param which is logged in server/proxy/browser.
    const url = `${getWsUrl()}?token=${encodeURIComponent(token)}`
    socket = new WebSocket(url)

    socket.onopen = () => {
      connected.value = true
      reconnectAttempts = 0
      // Clear pending queue — activeSubscriptions is the single source of truth.
      // Anything queued while disconnected was already added to activeSubscriptions
      // by subscribe(), so replaying pendingSubscriptions would cause duplicate sends.
      pendingSubscriptions = []
      // Re-subscribe to all active subscriptions (covers both reconnect and first-connect)
      for (const sub of activeSubscriptions) {
        try {
          socket!.send(JSON.stringify({ action: 'subscribe', channels: sub.channels, filters: sub.filters }))
        } catch (err) {
          console.error('WebSocket subscribe send failed (will retry on reconnect):', sanitizeForLog(err))
        }
      }
    }

    socket.onmessage = (event: MessageEvent) => {
      // .length counts UTF-16 code units, not bytes — close enough for size-gating
      if (typeof event.data === 'string' && event.data.length > WS_MAX_MESSAGE_SIZE) {
        console.error('WebSocket message exceeds max size, discarding')
        return
      }
      let data: unknown
      try {
        data = JSON.parse(event.data)
      } catch (parseErr) {
        console.error('Failed to parse WebSocket message:', sanitizeForLog(parseErr))
        return
      }

      const msg = data as Record<string, unknown>

      if (msg.action === 'subscribed' || msg.action === 'unsubscribed') {
        if (Array.isArray(msg.channels)) {
          subscribedChannels.value = [...(msg.channels as WsChannel[])]
        }
        return
      }

      if (msg.error) {
        console.error('WebSocket error:', sanitizeForLog(msg.error))
        return
      }

      if (msg.event_type && msg.channel) {
        try {
          dispatchEvent(msg as unknown as WsEvent)
        } catch (handlerErr) {
          console.error('WebSocket event handler error:', sanitizeForLog(handlerErr), 'Event type:', sanitizeForLog(msg.event_type, 100))
        }
      }
    }

    socket.onclose = () => {
      connected.value = false
      socket = null
      if (!intentionalClose && currentToken) {
        scheduleReconnect()
      }
    }

    socket.onerror = () => {
      console.error('WebSocket connection error')
      // onclose fires after onerror, reconnect is handled there
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    if (reconnectAttempts >= WS_MAX_RECONNECT_ATTEMPTS) {
      console.error('WebSocket: max reconnection attempts reached')
      reconnectExhausted.value = true
      return
    }
    const delay = Math.min(
      WS_RECONNECT_BASE_DELAY * Math.pow(2, reconnectAttempts),
      WS_RECONNECT_MAX_DELAY,
    )
    reconnectAttempts++
    reconnectTimer = setTimeout(() => {
      if (currentToken) connect(currentToken)
    }, delay)
  }

  function disconnect() {
    intentionalClose = true
    currentToken = null
    reconnectAttempts = 0
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (socket) {
      socket.close()
      socket = null
    }
    connected.value = false
    subscribedChannels.value = []
    pendingSubscriptions = []
    activeSubscriptions.length = 0
    channelHandlers.clear()
  }

  function subscribe(channels: WsChannel[], filters?: Record<string, string>) {
    // Track as active subscription for auto-re-subscribe on reconnect
    const key = subscriptionKey(channels, filters)
    if (!activeSubscriptions.some((s) => subscriptionKey(s.channels, s.filters) === key)) {
      activeSubscriptions.push({ channels: [...channels], filters: filters ? { ...filters } : undefined })
    }

    if (!socket || socket.readyState !== WebSocket.OPEN) {
      // Queue for replay when connection opens, with deduplication
      if (!pendingSubscriptions.some((s) => subscriptionKey(s.channels, s.filters) === key)) {
        pendingSubscriptions.push({ channels, filters })
      }
      return
    }
    try {
      socket.send(JSON.stringify({ action: 'subscribe', channels, filters }))
    } catch (err) {
      console.error('WebSocket subscribe send failed (queued for replay):', sanitizeForLog(err))
      if (!pendingSubscriptions.some((s) => subscriptionKey(s.channels, s.filters) === key)) {
        pendingSubscriptions.push({ channels, filters })
      }
    }
  }

  function unsubscribe(channels: WsChannel[]) {
    // Remove from tracked subscriptions so reconnect won't re-subscribe.
    // Uses every() — only removes subscriptions whose channels are fully
    // covered by the unsubscribe set. Partial overlap is intentionally kept.
    const channelSet = new Set(channels)
    for (let i = activeSubscriptions.length - 1; i >= 0; i--) {
      if (activeSubscriptions[i].channels.every((c) => channelSet.has(c))) {
        activeSubscriptions.splice(i, 1)
      }
    }
    for (let i = pendingSubscriptions.length - 1; i >= 0; i--) {
      if (pendingSubscriptions[i].channels.every((c) => channelSet.has(c))) {
        pendingSubscriptions.splice(i, 1)
      }
    }

    if (!socket || socket.readyState !== WebSocket.OPEN) return
    try {
      socket.send(JSON.stringify({ action: 'unsubscribe', channels }))
    } catch (err) {
      console.error('WebSocket unsubscribe send failed:', sanitizeForLog(err))
    }
  }

  function onChannelEvent(channel: string, handler: WsEventHandler) {
    if (!channelHandlers.has(channel)) {
      channelHandlers.set(channel, new Set())
    }
    channelHandlers.get(channel)!.add(handler)
  }

  function offChannelEvent(channel: string, handler: WsEventHandler) {
    channelHandlers.get(channel)?.delete(handler)
  }

  function dispatchEvent(event: WsEvent) {
    // Wrap each handler in try/catch so one failing handler doesn't block others
    channelHandlers.get(event.channel)?.forEach((h) => {
      try { h(event) } catch (err) {
        console.error('WebSocket channel handler error:', sanitizeForLog(err))
      }
    })
    channelHandlers.get('*')?.forEach((h) => {
      try { h(event) } catch (err) {
        console.error('WebSocket wildcard handler error:', sanitizeForLog(err))
      }
    })
  }

  return {
    connected,
    reconnectExhausted,
    subscribedChannels,
    connect,
    disconnect,
    subscribe,
    unsubscribe,
    onChannelEvent,
    offChannelEvent,
  }
})
