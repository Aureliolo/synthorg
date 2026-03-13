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
    error.value = null
    try {
      return await agentsApi.getAgent(name)
    } catch (err) {
      error.value = getErrorMessage(err)
      return null
    }
  }

  /** Runtime check for minimum required AgentConfig fields. */
  function isValidAgentPayload(p: Record<string, unknown>): boolean {
    return (
      typeof p.id === 'string' && p.id !== '' &&
      typeof p.name === 'string' && p.name !== '' &&
      typeof p.role === 'string' && p.role !== '' &&
      typeof p.department === 'string' && p.department !== ''
    )
  }

  function handleWsEvent(event: WsEvent) {
    const payload = event.payload as Record<string, unknown> | null
    if (!payload || typeof payload !== 'object') return
    switch (event.event_type) {
      case 'agent.hired':
        if (
          isValidAgentPayload(payload) &&
          !agents.value.some((a) => a.name === payload.name)
        ) {
          agents.value = [...agents.value, payload as unknown as AgentConfig]
          total.value++
        }
        break
      case 'agent.fired':
        if (typeof payload.name === 'string' && payload.name) {
          const prevLength = agents.value.length
          agents.value = agents.value.filter((a) => a.name !== payload.name)
          if (agents.value.length < prevLength) {
            total.value--
          }
        }
        break
      case 'agent.status_changed':
        if (typeof payload.name === 'string' && payload.name) {
          agents.value = agents.value.map((a) =>
            a.name === payload.name ? { ...a, ...(payload as Partial<AgentConfig>) } : a,
          )
        }
        break
    }
  }

  return { agents, total, loading, error, fetchAgents, fetchAgent, handleWsEvent }
})
