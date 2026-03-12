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
        <p class="text-xs text-slate-500">Team</p>
        <p class="text-sm text-slate-300">{{ agent.team ?? 'None' }}</p>
      </div>
      <div>
        <p class="text-xs text-slate-500">Seniority</p>
        <p class="text-sm text-slate-300">{{ formatLabel(agent.seniority) }}</p>
      </div>
      <div>
        <p class="text-xs text-slate-500">Model</p>
        <p class="text-sm text-slate-300">{{ agent.model }}</p>
      </div>
      <div>
        <p class="text-xs text-slate-500">Status</p>
        <p class="text-sm text-slate-300">{{ formatLabel(agent.status) }}</p>
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
          <p class="text-sm text-slate-300">{{ formatLabel(agent.personality.creativity_level) }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Decision Making</p>
          <p class="text-sm text-slate-300">{{ formatLabel(agent.personality.decision_making_style) }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Collaboration</p>
          <p class="text-sm text-slate-300">{{ formatLabel(agent.personality.collaboration_preference) }}</p>
        </div>
      </div>
    </div>

    <!-- Tools -->
    <div class="rounded-lg border border-slate-800 p-4">
      <h4 class="mb-3 text-sm font-medium text-slate-300">Tools ({{ agent.tools.length }})</h4>
      <div class="flex flex-wrap gap-2">
        <span
          v-for="tool in agent.tools"
          :key="tool"
          class="rounded bg-slate-800 px-2 py-1 text-xs text-slate-300"
        >
          {{ tool }}
        </span>
      </div>
    </div>

    <!-- Description -->
    <div v-if="agent.description" class="rounded-lg border border-slate-800 p-4">
      <h4 class="mb-2 text-sm font-medium text-slate-300">Description</h4>
      <p class="text-sm text-slate-400">{{ agent.description }}</p>
    </div>
  </div>
</template>
