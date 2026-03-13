<script setup lang="ts">
import type { AgentConfig } from '@/api/types'
import { formatLabel } from '@/utils/format'

defineProps<{
  agent: AgentConfig
}>()
</script>

<template>
  <div class="space-y-4">
    <!-- Basic Info -->
    <div class="grid grid-cols-2 gap-4 rounded-lg border border-slate-800 p-4">
      <div>
        <p class="text-xs text-slate-500">Role</p>
        <p class="text-sm text-slate-300">{{ agent.role }}</p>
      </div>
      <div>
        <p class="text-xs text-slate-500">Department</p>
        <p class="text-sm text-slate-300">{{ formatLabel(agent.department) }}</p>
      </div>
      <div>
        <p class="text-xs text-slate-500">Level</p>
        <p class="text-sm text-slate-300">{{ formatLabel(agent.level) }}</p>
      </div>
      <div>
        <p class="text-xs text-slate-500">Model</p>
        <p class="text-sm text-slate-300">{{ agent.model.model_id }}</p>
      </div>
      <div>
        <p class="text-xs text-slate-500">Status</p>
        <p class="text-sm text-slate-300">{{ formatLabel(agent.status) }}</p>
      </div>
      <div>
        <p class="text-xs text-slate-500">Autonomy</p>
        <p class="text-sm text-slate-300">{{ agent.autonomy_level ? formatLabel(agent.autonomy_level) : 'Default' }}</p>
      </div>
    </div>

    <!-- Personality -->
    <div class="rounded-lg border border-slate-800 p-4">
      <h4 class="mb-3 text-sm font-medium text-slate-300">Personality</h4>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <p class="text-xs text-slate-500">Risk Tolerance</p>
          <p class="text-sm text-slate-300">{{ formatLabel(agent.personality.risk_tolerance) }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Creativity</p>
          <p class="text-sm text-slate-300">{{ formatLabel(agent.personality.creativity) }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Decision Making</p>
          <p class="text-sm text-slate-300">{{ formatLabel(agent.personality.decision_making) }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Collaboration</p>
          <p class="text-sm text-slate-300">{{ formatLabel(agent.personality.collaboration) }}</p>
        </div>
      </div>
    </div>

    <!-- Tools -->
    <div class="rounded-lg border border-slate-800 p-4">
      <h4 class="mb-3 text-sm font-medium text-slate-300">Tools ({{ agent.tools.allowed.length }})</h4>
      <div v-if="agent.tools.allowed.length === 0" class="text-sm text-slate-500">
        No tools configured
      </div>
      <div v-else class="flex flex-wrap gap-2">
        <span
          v-for="tool in agent.tools.allowed"
          :key="tool"
          class="rounded bg-slate-800 px-2 py-1 text-xs text-slate-300"
        >
          {{ tool }}
        </span>
      </div>
    </div>
  </div>
</template>
