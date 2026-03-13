<script setup lang="ts">
import type { HealthStatus } from '@/api/types'
import { formatUptime } from '@/utils/format'

defineProps<{
  health: HealthStatus | null
  wsConnected: boolean
}>()
</script>

<template>
  <div class="rounded-lg border border-slate-800 bg-slate-900 p-5">
    <h3 class="mb-4 text-sm font-medium text-slate-300">System Status</h3>

    <div class="space-y-3">
      <!-- API Status -->
      <div class="flex items-center justify-between">
        <span class="text-sm text-slate-400">API Server</span>
        <span
          :class="[
            'text-sm font-medium',
            health?.status === 'ok' ? 'text-green-400' : health ? 'text-yellow-400' : 'text-red-400',
          ]"
        >
          {{ health?.status ?? 'Unreachable' }}
        </span>
      </div>

      <!-- Persistence -->
      <div class="flex items-center justify-between">
        <span class="text-sm text-slate-400">Persistence</span>
        <span
          :class="['text-sm font-medium', health?.persistence ? 'text-green-400' : health ? 'text-red-400' : 'text-slate-400']"
        >
          {{ health ? (health.persistence ? 'OK' : 'Down') : 'Unknown' }}
        </span>
      </div>

      <!-- Message Bus -->
      <div class="flex items-center justify-between">
        <span class="text-sm text-slate-400">Message Bus</span>
        <span
          :class="['text-sm font-medium', health?.message_bus ? 'text-green-400' : health ? 'text-red-400' : 'text-slate-400']"
        >
          {{ health ? (health.message_bus ? 'OK' : 'Down') : 'Unknown' }}
        </span>
      </div>

      <!-- WebSocket -->
      <div class="flex items-center justify-between">
        <span class="text-sm text-slate-400">WebSocket</span>
        <span
          :class="['text-sm font-medium', wsConnected ? 'text-green-400' : 'text-red-400']"
        >
          {{ wsConnected ? 'Connected' : 'Disconnected' }}
        </span>
      </div>

      <!-- Uptime -->
      <div v-if="health" class="flex items-center justify-between border-t border-slate-800 pt-3">
        <span class="text-sm text-slate-400">Uptime</span>
        <span class="text-sm text-slate-300">{{ formatUptime(health.uptime_seconds) }}</span>
      </div>

      <!-- Version -->
      <div v-if="health" class="flex items-center justify-between">
        <span class="text-sm text-slate-400">Version</span>
        <span class="text-sm text-slate-300">{{ health.version }}</span>
      </div>
    </div>
  </div>
</template>
