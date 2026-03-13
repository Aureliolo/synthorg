<script setup lang="ts">
import type { Message } from '@/api/types'
import { formatRelativeTime } from '@/utils/format'

defineProps<{
  message: Message
}>()
</script>

<template>
  <div class="rounded border border-slate-800 p-3">
    <div class="mb-1 flex items-center justify-between">
      <span class="text-sm font-medium text-brand-400">{{ message.sender }}</span>
      <span class="text-xs text-slate-500">{{ formatRelativeTime(message.timestamp) }}</span>
    </div>
    <p class="text-sm text-slate-300 whitespace-pre-wrap">{{ message.content }}</p>
    <span class="mt-1 inline-block text-xs text-slate-600">{{ message.channel }}</span>
  </div>
</template>
