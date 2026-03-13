import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as agentsApi from '@/api/endpoints/agents'
import { getErrorMessage } from '@/utils/errors'
import { MAX_PAGE_SIZE } from '@/utils/constants'
import type { AgentConfig, WsEvent } from '@/api/types'

export const useAgentStore = defineStore('agents', () => {
  const agents = ref<AgentConfig[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchAgents() {
    loading.value = true
    error.value = null
    try {
      const result = await agentsApi.listAgents({ limit: MAX_PAGE_SIZE })
      agents.value = result.data
      total.value = result.total
    } catch (err) {
      error.value = getErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function fetchAgent(name: string): Promise<AgentConfig | null> {
    try {
      return await agentsApi.getAgent(name)
    } catch (err) {
      error.value = getErrorMessage(err)
      return null
    }
  }

  function handleWsEvent(event: WsEvent) {
    const payload = event.payload as Partial<AgentConfig> & { name?: string }
    switch (event.event_type) {
      case 'agent.hired':
        if (
          typeof payload.name === 'string' &&
          payload.name &&
          !agents.value.some((a) => a.name === payload.name)
        ) {
          // Only append if payload has required fields
          if (payload.id && payload.role && payload.department) {
            agents.value = [...agents.value, payload as AgentConfig]
            total.value++
          }
        }
        break
      case 'agent.fired':
        if (payload.name) {
          const prevLength = agents.value.length
          agents.value = agents.value.filter((a) => a.name !== payload.name)
          if (agents.value.length < prevLength) {
            total.value--
          }
        }
        break
      case 'agent.status_changed':
        if (payload.name) {
          agents.value = agents.value.map((a) =>
            a.name === payload.name ? { ...a, ...payload } : a,
          )
        }
        break
    }
  }

  return { agents, total, loading, error, fetchAgents, fetchAgent, handleWsEvent }
})
