import { ref, onUnmounted } from 'vue'
import type { WsChannel, WsEvent, WsSubscribeMessage, WsUnsubscribeMessage } from '@/api/types'
import { WS_RECONNECT_BASE_DELAY, WS_RECONNECT_MAX_DELAY, WS_MAX_RECONNECT_ATTEMPTS } from '@/utils/constants'

export type WsEventHandler = (event: WsEvent) => void

export function useWebSocket() {
  const connected = ref(false)
  const subscribedChannels = ref<WsChannel[]>([])

  let socket: WebSocket | null = null
  let reconnectAttempts = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let intentionalClose = false
  const eventHandlers = new Map<string, Set<WsEventHandler>>()
  let pendingSubscriptions: { channels: WsChannel[]; filters?: Record<string, string> }[] = []

  function getWsUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    return `${protocol}//${host}/api/v1/ws`
  }

  function connect(token: string) {
    if (socket?.readyState === WebSocket.OPEN) return

    intentionalClose = false
    // TODO: Replace with one-time WS ticket endpoint for production security.
    // Currently passes JWT as query param which is logged in server/proxy/browser.
    // Secure pattern: POST /api/v1/auth/ws-ticket -> single-use opaque ticket
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

    socket.onmessage = (event) => {
      let data: unknown
      try {
        data = JSON.parse(event.data)
      } catch (parseErr) {
        console.error('Failed to parse WebSocket message:', parseErr)
        return
      }

      const msg = data as Record<string, unknown>

      // Handle ack messages
      if (msg.action === 'subscribed' || msg.action === 'unsubscribed') {
        subscribedChannels.value = [...(msg.channels as WsChannel[])]
        return
      }

      // Handle error messages
      if (msg.error) {
        console.error('WebSocket error:', msg.error)
        return
      }

      // Handle events — catch handler errors separately
      if (msg.event_type && msg.channel) {
        try {
          dispatchEvent(msg as unknown as WsEvent)
        } catch (handlerErr) {
          console.error('WebSocket event handler error:', handlerErr, 'Event:', msg)
        }
      }
    }

    socket.onclose = () => {
      connected.value = false
      socket = null
      if (!intentionalClose) {
        scheduleReconnect(token)
      }
    }

    socket.onerror = (event) => {
      console.error('WebSocket connection error:', event)
      // onclose will fire after onerror, reconnect is handled there
    }
  }

  function scheduleReconnect(token: string) {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    if (reconnectAttempts >= WS_MAX_RECONNECT_ATTEMPTS) {
      console.error('WebSocket: max reconnection attempts reached')
      return
    }
    const delay = Math.min(
      WS_RECONNECT_BASE_DELAY * Math.pow(2, reconnectAttempts),
      WS_RECONNECT_MAX_DELAY,
    )
    reconnectAttempts++
    reconnectTimer = setTimeout(() => connect(token), delay)
  }

  function disconnect() {
    intentionalClose = true
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
    const msg: WsSubscribeMessage = { action: 'subscribe', channels, filters }
    socket.send(JSON.stringify(msg))
  }

  function unsubscribe(channels: WsChannel[]) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return
    const msg: WsUnsubscribeMessage = { action: 'unsubscribe', channels }
    socket.send(JSON.stringify(msg))
  }

  function onEvent(channel: string, handler: WsEventHandler) {
    if (!eventHandlers.has(channel)) {
      eventHandlers.set(channel, new Set())
    }
    eventHandlers.get(channel)!.add(handler)
  }

  function offEvent(channel: string, handler: WsEventHandler) {
    eventHandlers.get(channel)?.delete(handler)
  }

  function dispatchEvent(event: WsEvent) {
    // Dispatch to channel-specific handlers
    eventHandlers.get(event.channel)?.forEach((handler) => handler(event))
    // Dispatch to wildcard handlers
    eventHandlers.get('*')?.forEach((handler) => handler(event))
  }

  onUnmounted(() => {
    disconnect()
  })

  return {
    connected,
    subscribedChannels,
    connect,
    disconnect,
    subscribe,
    unsubscribe,
    onEvent,
    offEvent,
  }
}
