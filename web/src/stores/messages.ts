import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as messagesApi from '@/api/endpoints/messages'
import { getErrorMessage } from '@/utils/errors'
import type { Channel, Message, WsEvent } from '@/api/types'

const MAX_WS_MESSAGES = 500

/** Runtime check for minimum required Message fields on a WS payload. */
function isValidMessagePayload(p: Record<string, unknown>): boolean {
  return (
    typeof p.id === 'string' && p.id !== '' &&
    typeof p.channel === 'string' &&
    typeof p.sender === 'string' &&
    typeof p.content === 'string' &&
    typeof p.timestamp === 'string'
  )
}

export const useMessageStore = defineStore('messages', () => {
  const messages = ref<Message[]>([])
  const channels = ref<Channel[]>([])
  const total = ref(0)
  const activeChannel = ref<string | null>(null)
  const loading = ref(false)
  const channelsLoading = ref(false)
  const error = ref<string | null>(null)
  const channelsError = ref<string | null>(null)
  let fetchRequestId = 0

  async function fetchChannels() {
    channelsLoading.value = true
    channelsError.value = null
    try {
      channels.value = await messagesApi.listChannels()
    } catch (err) {
      channelsError.value = getErrorMessage(err)
    } finally {
      channelsLoading.value = false
    }
  }

  async function fetchMessages(channel?: string) {
    const requestId = ++fetchRequestId
    loading.value = true
    error.value = null
    // Sync the WS filter so handleWsEvent matches the fetched channel
    activeChannel.value = channel ?? null
    try {
      const params = channel ? { channel, limit: 100 } : { limit: 100 }
      const result = await messagesApi.listMessages(params)
      // Only commit if this is still the latest request (prevent stale overwrites)
      if (requestId === fetchRequestId) {
        messages.value = result.data
        total.value = result.total
      }
    } catch (err) {
      if (requestId === fetchRequestId) {
        error.value = getErrorMessage(err)
      }
    } finally {
      if (requestId === fetchRequestId) {
        loading.value = false
      }
    }
  }

  function setActiveChannel(channel: string | null) {
    activeChannel.value = channel
  }

  function handleWsEvent(event: WsEvent) {
    if (event.event_type === 'message.sent') {
      const payload = event.payload as Record<string, unknown> | null
      if (!payload || typeof payload !== 'object') return
      if (!isValidMessagePayload(payload)) return
      const message = payload as unknown as Message
      // Only append if message matches active channel (or no filter is set)
      if (!activeChannel.value || message.channel === activeChannel.value) {
        if (!messages.value.some((m) => m.id === message.id)) {
          messages.value = [...messages.value, message].slice(-MAX_WS_MESSAGES)
        }
      }
    }
  }

  return {
    messages,
    channels,
    total,
    activeChannel,
    loading,
    channelsLoading,
    error,
    channelsError,
    fetchChannels,
    fetchMessages,
    setActiveChannel,
    handleWsEvent,
  }
})
