<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import MessageItem from './MessageItem.vue'
import type { Message } from '@/api/types'

const props = defineProps<{
  messages: Message[]
}>()

const listRef = ref<HTMLElement | null>(null)

watch(
  () => props.messages.length,
  async (_newLen, _oldLen) => {
    if (!listRef.value) return
    // Capture scroll position BEFORE DOM update to avoid stale scrollHeight
    const { scrollTop, scrollHeight, clientHeight } = listRef.value
    const isNearBottom = scrollTop + clientHeight >= scrollHeight - 100
    await nextTick()
    if (isNearBottom && listRef.value) {
      listRef.value.scrollTop = listRef.value.scrollHeight
    }
  },
)
</script>

<template>
  <div ref="listRef" role="log" aria-live="polite" class="space-y-2 overflow-y-auto" style="max-height: calc(100vh - 280px)">
    <MessageItem
      v-for="msg in messages"
      :key="msg.id"
      :message="msg"
    />
  </div>
</template>
