import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { WsChannel, WsEvent, WsEventHandler } from '@/api/types'
import { WS_RECONNECT_BASE_DELAY, WS_RECONNECT_MAX_DELAY, WS_MAX_RECONNECT_ATTEMPTS } from '@/utils/constants'

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
    // TODO: Replace with one-time WS ticket endpoint for production security.
    // Currently passes JWT as query param which is logged in server/proxy/browser.
    const url = `${getWsUrl()}?token=${encodeURIComponent(token)}`
    socket = new WebSocket(url)

    socket.onopen = () => {
      connected.value = true
      reconnectAttempts = 0
      // Replay any subscriptions that were queued while disconnected
      for (const pending of pendingSubscriptions) {
        subscribe(pending.channels, pending.filters)
      }
      pendingSubscriptions = []
    }

    socket.onmessage = (event: MessageEvent) => {
      let data: unknown
      try {
        data = JSON.parse(event.data)
      } catch (parseErr) {
        console.error('Failed to parse WebSocket message:', parseErr)
        return
      }

      const msg = data as Record<string, unknown>

      if (msg.action === 'subscribed' || msg.action === 'unsubscribed') {
        subscribedChannels.value = [...(msg.channels as WsChannel[])]
        return
      }

      if (msg.error) {
        console.error('WebSocket error:', String(msg.error).slice(0, 200))
        return
      }

      if (msg.event_type && msg.channel) {
        try {
          dispatchEvent(msg as unknown as WsEvent)
        } catch (handlerErr) {
          console.error('WebSocket event handler error:', handlerErr, 'Event type:', String(msg.event_type))
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

    socket.onerror = (event) => {
      console.error('WebSocket connection error:', event)
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
  }

  function subscribe(channels: WsChannel[], filters?: Record<string, string>) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      // Queue for replay when connection opens
      pendingSubscriptions.push({ channels, filters })
      return
    }
    socket.send(JSON.stringify({ action: 'subscribe', channels, filters }))
  }

  function unsubscribe(channels: WsChannel[]) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return
    socket.send(JSON.stringify({ action: 'unsubscribe', channels }))
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
    channelHandlers.get(event.channel)?.forEach((h) => h(event))
    channelHandlers.get('*')?.forEach((h) => h(event))
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
