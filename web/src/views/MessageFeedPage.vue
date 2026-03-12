<script setup lang="ts">
import { onMounted, onUnmounted, watch } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import MessageList from '@/components/messages/MessageList.vue'
import ChannelSelector from '@/components/messages/ChannelSelector.vue'
import { useMessageStore } from '@/stores/messages'
import { useWebSocketStore } from '@/stores/websocket'
import { useAuthStore } from '@/stores/auth'

const messageStore = useMessageStore()
const wsStore = useWebSocketStore()
const authStore = useAuthStore()

onMounted(async () => {
  if (authStore.token && !wsStore.connected) {
    wsStore.connect(authStore.token)
  }
  wsStore.subscribe(['messages'])
  wsStore.onChannelEvent('messages', messageStore.handleWsEvent)
  await Promise.all([messageStore.fetchChannels(), messageStore.fetchMessages()])
})

onUnmounted(() => {
  wsStore.offChannelEvent('messages', messageStore.handleWsEvent)
})

watch(
  () => messageStore.activeChannel,
  (channel) => {
    messageStore.fetchMessages(channel ?? undefined)
  },
)

function handleChannelChange(channel: string | null) {
  messageStore.setActiveChannel(channel)
}
</script>

<template>
  <AppShell>
    <PageHeader title="Messages" subtitle="Real-time communication feed">
      <template #actions>
        <ChannelSelector
          :model-value="messageStore.activeChannel"
          :channels="messageStore.channels"
          @update:model-value="handleChannelChange"
        />
      </template>
    </PageHeader>

    <ErrorBoundary :error="messageStore.error" @retry="messageStore.fetchMessages()">
      <LoadingSkeleton v-if="messageStore.loading && messageStore.messages.length === 0" :lines="6" />
      <EmptyState
        v-else-if="messageStore.messages.length === 0"
        icon="pi pi-comments"
        title="No messages"
        message="Messages from agents will appear here in real-time."
      />
      <MessageList v-else :messages="messageStore.messages" />
    </ErrorBoundary>
  </AppShell>
</template>
