<script setup lang="ts">
import StatusBadge from '@/components/common/StatusBadge.vue'
import type { AgentConfig } from '@/api/types'
import { formatLabel } from '@/utils/format'

defineProps<{
  agent: AgentConfig
}>()

defineEmits<{
  click: [agent: AgentConfig]
}>()
</script>

<template>
  <div
    role="button"
    tabindex="0"
    class="cursor-pointer rounded-lg border border-slate-800 bg-slate-900 p-4 transition-colors hover:border-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500"
    @click="$emit('click', agent)"
    @keydown.enter="$emit('click', agent)"
    @keydown.space.prevent="$emit('click', agent)"
  >
    <div class="mb-3 flex items-start justify-between">
      <div>
        <h4 class="font-medium text-slate-200">{{ agent.name }}</h4>
        <p class="text-sm text-slate-400">{{ agent.role }}</p>
      </div>
      <StatusBadge :value="agent.status" />
    </div>
    <div class="space-y-1 text-xs text-slate-500">
      <div class="flex justify-between">
        <span>Department</span>
        <span class="text-slate-300">{{ formatLabel(agent.department) }}</span>
      </div>
      <div class="flex justify-between">
        <span>Level</span>
        <span class="text-slate-300">{{ formatLabel(agent.level) }}</span>
      </div>
      <div class="flex justify-between">
        <span>Model</span>
        <span class="text-slate-300">{{ agent.model.model_id }}</span>
      </div>
    </div>
  </div>
</template>
