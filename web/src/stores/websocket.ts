import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { WsChannel, WsEvent } from '@/api/types'
import type { WsEventHandler } from '@/composables/useWebSocket'
import { WS_RECONNECT_BASE_DELAY, WS_RECONNECT_MAX_DELAY } from '@/utils/constants'

export const useWebSocketStore = defineStore('websocket', () => {
  const connected = ref(false)
  const subscribedChannels = ref<WsChannel[]>([])

  let socket: WebSocket | null = null
  let reconnectAttempts = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let intentionalClose = false
  let currentToken: string | null = null
  const channelHandlers = new Map<string, Set<WsEventHandler>>()

  function getWsUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    return `${protocol}//${host}/api/v1/ws`
  }

  function connect(token: string) {
    if (socket?.readyState === WebSocket.OPEN) return

    currentToken = token
    intentionalClose = false
    const url = `${getWsUrl()}?token=${encodeURIComponent(token)}`
    socket = new WebSocket(url)

    socket.onopen = () => {
      connected.value = true
      reconnectAttempts = 0
    }

    socket.onmessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data)

        if (data.action === 'subscribed' || data.action === 'unsubscribed') {
          subscribedChannels.value = [...data.channels]
          return
        }

        if (data.error) {
          console.error('WebSocket error:', data.error)
          return
        }

        if (data.event_type && data.channel) {
          dispatchEvent(data as WsEvent)
        }
      } catch {
        console.error('Failed to parse WebSocket message')
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
      // onclose fires after onerror
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer)
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
  }

  function subscribe(channels: WsChannel[], filters?: Record<string, string>) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return
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
    subscribedChannels,
    connect,
    disconnect,
    subscribe,
    unsubscribe,
    onChannelEvent,
    offChannelEvent,
  }
})
