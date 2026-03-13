<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getHealth } from '@/api/endpoints/health'
import { useWebSocketStore } from '@/stores/websocket'
import { usePolling } from '@/composables/usePolling'
import { HEALTH_POLL_INTERVAL } from '@/utils/constants'
import type { HealthStatus } from '@/api/types'

const wsStore = useWebSocketStore()
const health = ref<HealthStatus | null>(null)
const healthError = ref(false)

async function checkHealth() {
  try {
    health.value = await getHealth()
    healthError.value = false
  } catch {
    healthError.value = true
    health.value = null
  }
}

const { start } = usePolling(checkHealth, HEALTH_POLL_INTERVAL)
onMounted(start)
</script>

<template>
  <div class="flex items-center gap-3 text-xs" role="status" aria-live="polite">
    <!-- API Status -->
    <div
      class="flex items-center gap-1.5"
      :aria-label="'API: ' + (healthError ? 'error' : health?.status ?? 'unknown')"
    >
      <span
        :class="[
          'inline-block h-2 w-2 rounded-full',
          healthError
            ? 'bg-red-500'
            : health?.status === 'ok'
              ? 'bg-green-500'
              : health?.status === 'degraded'
                ? 'bg-yellow-500'
                : 'bg-gray-500',
        ]"
        aria-hidden="true"
      />
      <span class="text-slate-400">API</span>
    </div>

    <!-- WebSocket Status -->
    <div
      class="flex items-center gap-1.5"
      :aria-label="'WebSocket: ' + (wsStore.connected ? 'connected' : 'disconnected')"
    >
      <span
        :class="[
          'inline-block h-2 w-2 rounded-full',
          wsStore.connected ? 'bg-green-500' : 'bg-red-500',
        ]"
        aria-hidden="true"
      />
      <span class="text-slate-400">WS</span>
    </div>
  </div>
</template>
