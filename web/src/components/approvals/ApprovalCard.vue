<script setup lang="ts">
import StatusBadge from '@/components/common/StatusBadge.vue'
import type { ApprovalItem } from '@/api/types'
import { formatRelativeTime } from '@/utils/format'

defineProps<{
  approval: ApprovalItem
}>()

defineEmits<{
  click: [approval: ApprovalItem]
}>()
</script>

<template>
  <div
    role="button"
    tabindex="0"
    class="cursor-pointer rounded-lg border border-slate-800 bg-slate-900 p-4 transition-colors hover:border-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500"
    @click="$emit('click', approval)"
    @keydown.enter="$emit('click', approval)"
    @keydown.space.prevent="$emit('click', approval)"
  >
    <div class="mb-2 flex items-start justify-between">
      <h4 class="text-sm font-medium text-slate-200">{{ approval.title }}</h4>
      <div class="flex gap-2">
        <StatusBadge :value="approval.risk_level" type="risk" />
        <StatusBadge :value="approval.status" />
      </div>
    </div>
    <p class="mb-3 text-xs text-slate-400 line-clamp-2">{{ approval.description }}</p>
    <div class="flex items-center justify-between text-xs text-slate-500">
      <span>{{ approval.requested_by }} &middot; {{ approval.action_type }}</span>
      <span>{{ formatRelativeTime(approval.created_at) }}</span>
    </div>
  </div>
</template>
