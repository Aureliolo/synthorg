<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { getHealth } from '@/api/endpoints/health'
import { useWebSocketStore } from '@/stores/websocket'
import { HEALTH_POLL_INTERVAL } from '@/utils/constants'
import type { HealthStatus } from '@/api/types'

const wsStore = useWebSocketStore()
const health = ref<HealthStatus | null>(null)
const healthError = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

async function checkHealth() {
  try {
    health.value = await getHealth()
    healthError.value = false
  } catch {
    healthError.value = true
    health.value = null
  }
}

onMounted(() => {
  checkHealth()
  pollTimer = setInterval(checkHealth, HEALTH_POLL_INTERVAL)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div class="flex items-center gap-3 text-xs">
    <!-- API Status -->
    <div class="flex items-center gap-1.5">
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
      />
      <span class="text-slate-400">API</span>
    </div>

    <!-- WebSocket Status -->
    <div class="flex items-center gap-1.5">
      <span
        :class="[
          'inline-block h-2 w-2 rounded-full',
          wsStore.connected ? 'bg-green-500' : 'bg-red-500',
        ]"
      />
      <span class="text-slate-400">WS</span>
    </div>
  </div>
</template>
