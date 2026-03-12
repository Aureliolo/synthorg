import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as messagesApi from '@/api/endpoints/messages'
import type { Channel, Message, WsEvent } from '@/api/types'

export const useMessageStore = defineStore('messages', () => {
  const messages = ref<Message[]>([])
  const channels = ref<Channel[]>([])
  const total = ref(0)
  const activeChannel = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchChannels() {
    try {
      channels.value = await messagesApi.listChannels()
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load channels'
    }
  }

  async function fetchMessages(channel?: string) {
    loading.value = true
    error.value = null
    try {
      const params = channel ? { channel, limit: 100 } : { limit: 100 }
      const result = await messagesApi.listMessages(params)
      messages.value = result.data
      total.value = result.total
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load messages'
    } finally {
      loading.value = false
    }
  }

  function setActiveChannel(channel: string | null) {
    activeChannel.value = channel
  }

  function handleWsEvent(event: WsEvent) {
    if (event.event_type === 'message.sent') {
      const message = event.payload as unknown as Message
      if (message.id) {
        messages.value = [...messages.value, message]
        total.value++
      }
    }
  }

  return {
    messages,
    channels,
    total,
    activeChannel,
    loading,
    error,
    fetchChannels,
    fetchMessages,
    setActiveChannel,
    handleWsEvent,
  }
})
