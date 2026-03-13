<script setup lang="ts">
import { RouterLink } from 'vue-router'
import StatusBadge from '@/components/common/StatusBadge.vue'
import type { ApprovalItem } from '@/api/types'
import { formatRelativeTime } from '@/utils/format'

defineProps<{
  approvals: ApprovalItem[]
}>()
</script>

<template>
  <div class="rounded-lg border border-slate-800 bg-slate-900 p-5">
    <div class="mb-4 flex items-center justify-between">
      <h3 class="text-sm font-medium text-slate-300">Recent Approvals</h3>
      <RouterLink to="/approvals" class="text-xs text-brand-400 hover:text-brand-300">
        View all
      </RouterLink>
    </div>
    <div v-if="approvals.length === 0" class="py-4 text-center text-sm text-slate-500">
      No recent approvals
    </div>
    <div v-else class="space-y-3">
      <div
        v-for="item in approvals.slice(0, 5)"
        :key="item.id"
        class="flex items-center justify-between rounded border border-slate-800 p-3"
      >
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm text-slate-200">{{ item.title }}</p>
          <p class="mt-0.5 text-xs text-slate-500">{{ item.requested_by }}</p>
        </div>
        <div class="ml-3 flex items-center gap-2">
          <StatusBadge :value="item.risk_level" type="risk" />
          <StatusBadge :value="item.status" />
          <span class="text-xs text-slate-500">{{ formatRelativeTime(item.created_at) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
