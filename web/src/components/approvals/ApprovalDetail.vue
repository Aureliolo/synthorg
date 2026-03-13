<script setup lang="ts">
import StatusBadge from '@/components/common/StatusBadge.vue'
import type { ApprovalItem } from '@/api/types'
import { formatDate } from '@/utils/format'

defineProps<{
  approval: ApprovalItem
}>()
</script>

<template>
  <div class="space-y-4">
    <div>
      <h3 class="text-lg font-medium text-slate-100">{{ approval.title }}</h3>
      <p class="mt-2 text-sm text-slate-400 whitespace-pre-wrap">{{ approval.description }}</p>
    </div>

    <div class="grid grid-cols-2 gap-4 rounded-lg border border-slate-800 p-4">
      <div>
        <p class="text-xs text-slate-500">Status</p>
        <StatusBadge :value="approval.status" />
      </div>
      <div>
        <p class="text-xs text-slate-500">Risk Level</p>
        <StatusBadge :value="approval.risk_level" type="risk" />
      </div>
      <div>
        <p class="text-xs text-slate-500">Action Type</p>
        <p class="text-sm text-slate-300">{{ approval.action_type }}</p>
      </div>
      <div>
        <p class="text-xs text-slate-500">Requested By</p>
        <p class="text-sm text-slate-300">{{ approval.requested_by }}</p>
      </div>
      <div>
        <p class="text-xs text-slate-500">Created</p>
        <p class="text-sm text-slate-300">{{ formatDate(approval.created_at) }}</p>
      </div>
      <div>
        <p class="text-xs text-slate-500">Expires</p>
        <p class="text-sm text-slate-300">{{ formatDate(approval.expires_at) }}</p>
      </div>
      <div v-if="approval.decided_by">
        <p class="text-xs text-slate-500">Decided By</p>
        <p class="text-sm text-slate-300">{{ approval.decided_by }}</p>
      </div>
      <div v-if="approval.decided_at">
        <p class="text-xs text-slate-500">Decided At</p>
        <p class="text-sm text-slate-300">{{ formatDate(approval.decided_at) }}</p>
      </div>
    </div>

    <div v-if="approval.decision_reason" class="rounded-lg border border-slate-800 p-4">
      <p class="text-xs text-slate-500">Decision Comment</p>
      <p class="mt-1 text-sm text-slate-300">{{ approval.decision_reason }}</p>
    </div>

    <div v-if="Object.keys(approval.metadata).length > 0" class="rounded-lg border border-slate-800 p-4">
      <p class="mb-2 text-xs text-slate-500">Metadata</p>
      <div v-for="(value, key) in approval.metadata" :key="key" class="flex justify-between text-sm">
        <span class="text-slate-400">{{ key }}</span>
        <span class="text-slate-300">{{ value }}</span>
      </div>
    </div>
  </div>
</template>
