<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import AppShell from '@/components/layout/AppShell.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import AgentMetrics from '@/components/agents/AgentMetrics.vue'
import { useAgentStore } from '@/stores/agents'
import { getErrorMessage } from '@/utils/errors'
import type { AgentConfig } from '@/api/types'

const props = defineProps<{
  name: string
}>()

const router = useRouter()
const agentStore = useAgentStore()
const agent = ref<AgentConfig | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

const AGENT_NAME_RE = /^[\w][\w.\-]{0,63}$/

async function fetchAgentData() {
  loading.value = true
  error.value = null
  if (!AGENT_NAME_RE.test(props.name)) {
    error.value = 'Invalid agent name'
    loading.value = false
    return
  }
  try {
    agent.value = await agentStore.fetchAgent(props.name)
    if (!agent.value) {
      error.value = `Agent "${props.name}" not found`
    }
  } catch (err) {
    error.value = getErrorMessage(err)
  } finally {
    loading.value = false
  }
}

watch(() => props.name, fetchAgentData, { immediate: true })
</script>

<template>
  <AppShell>
    <div class="mb-4">
      <Button
        label="Back to Agents"
        icon="pi pi-arrow-left"
        text
        size="small"
        @click="router.push('/agents')"
      />
    </div>

    <ErrorBoundary :error="error" @retry="fetchAgentData">
      <LoadingSkeleton v-if="loading" :lines="8" />
      <template v-else-if="agent">
        <PageHeader :title="agent.name" :subtitle="agent.role" />
        <AgentMetrics :agent="agent" />
      </template>
    </ErrorBoundary>
  </AppShell>
</template>
