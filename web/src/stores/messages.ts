import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as messagesApi from '@/api/endpoints/messages'
import { getErrorMessage } from '@/utils/errors'
import type { Channel, Message, WsEvent } from '@/api/types'

const MAX_WS_MESSAGES = 500

export const useMessageStore = defineStore('messages', () => {
  const messages = ref<Message[]>([])
  const channels = ref<Channel[]>([])
  const total = ref(0)
  const activeChannel = ref<string | null>(null)
  const loading = ref(false)
  const channelsLoading = ref(false)
  const error = ref<string | null>(null)
  const channelsError = ref<string | null>(null)

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
    loading.value = true
    error.value = null
    try {
      const params = channel ? { channel, limit: 100 } : { limit: 100 }
      const result = await messagesApi.listMessages(params)
      messages.value = result.data
      total.value = result.total
    } catch (err) {
      error.value = getErrorMessage(err)
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
        // Only append if message matches active channel (or no filter is set)
        if (!activeChannel.value || message.channel === activeChannel.value) {
          messages.value = [...messages.value, message].slice(-MAX_WS_MESSAGES)
        }
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
    channelsLoading,
    error,
    channelsError,
    fetchChannels,
    fetchMessages,
    setActiveChannel,
    handleWsEvent,
  }
})
