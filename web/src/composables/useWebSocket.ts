import { ref, onUnmounted } from 'vue'
import type { WsChannel, WsEvent, WsSubscribeMessage, WsUnsubscribeMessage } from '@/api/types'
import { WS_RECONNECT_BASE_DELAY, WS_RECONNECT_MAX_DELAY } from '@/utils/constants'

export type WsEventHandler = (event: WsEvent) => void

export function useWebSocket() {
  const connected = ref(false)
  const subscribedChannels = ref<WsChannel[]>([])

  let socket: WebSocket | null = null
  let reconnectAttempts = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let intentionalClose = false
  const eventHandlers = new Map<string, Set<WsEventHandler>>()

  function getWsUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    return `${protocol}//${host}/api/v1/ws`
  }

  function connect(token: string) {
    if (socket?.readyState === WebSocket.OPEN) return

    intentionalClose = false
    const url = `${getWsUrl()}?token=${encodeURIComponent(token)}`
    socket = new WebSocket(url)

    socket.onopen = () => {
      connected.value = true
      reconnectAttempts = 0
    }

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)

        // Handle ack messages
        if (data.action === 'subscribed' || data.action === 'unsubscribed') {
          subscribedChannels.value = [...data.channels]
          return
        }

        // Handle error messages
        if (data.error) {
          console.error('WebSocket error:', data.error)
          return
        }

        // Handle events
        if (data.event_type && data.channel) {
          const wsEvent = data as WsEvent
          dispatchEvent(wsEvent)
        }
      } catch {
        console.error('Failed to parse WebSocket message')
      }
    }

    socket.onclose = () => {
      connected.value = false
      socket = null
      if (!intentionalClose) {
        scheduleReconnect(token)
      }
    }

    socket.onerror = () => {
      // onclose will fire after onerror
    }
  }

  function scheduleReconnect(token: string) {
    if (reconnectTimer) clearTimeout(reconnectTimer)
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
  }

  function subscribe(channels: WsChannel[], filters?: Record<string, string>) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return
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
