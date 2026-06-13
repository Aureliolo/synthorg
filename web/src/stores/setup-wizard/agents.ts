import {
  getAgents,
  listPersonalityPresets,
  randomizeAgentName as apiRandomizeAgentName,
  updateAgentModel as apiUpdateAgentModel,
  updateAgentName as apiUpdateAgentName,
  updateAgentPersonality as apiUpdateAgentPersonality,
} from '@/api/endpoints/setup'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import type { AgentsSlice, SliceCreator } from './types'

const log = createLogger('setup-wizard:agents')

type WizSet = Parameters<SliceCreator<AgentsSlice>>[0]

/**
 * Report a failed in-wizard agent mutation. Agent cards sit mid-list inside a
 * scrollable step, so the top-of-page agentsError banner alone can scroll out
 * of view; the toast gives point-of-interaction feedback regardless of scroll.
 */
function reportAgentUpdateError(set: WizSet, action: string, err: unknown): void {
  const msg = getErrorMessage(err)
  log.error(`${action} failed:`, msg)
  set({ agentsError: msg })
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(err, 'Could not update agent'),
    description: msg,
  })
}

export const createAgentsSlice: SliceCreator<AgentsSlice> = (set) => ({
  agents: [],
  agentsLoading: false,
  agentsError: null,
  personalityPresets: [],
  personalityPresetsLoading: false,
  personalityPresetsError: null,

  async fetchAgents() {
    set({ agentsLoading: true, agentsError: null })
    try {
      const agents = await getAgents()
      set({ agents: [...agents], agentsLoading: false })
    } catch (err) {
      log.error('fetchAgents failed:', getErrorMessage(err))
      set({ agentsError: getErrorMessage(err), agentsLoading: false })
    }
  },

  async updateAgentModel(index, provider, modelId) {
    set({ agentsError: null })
    try {
      const updated = await apiUpdateAgentModel(index, {
        model_provider: provider,
        model_id: modelId,
      })
      set((s) => ({ agents: s.agents.map((a, i) => (i === index ? updated : a)) }))
    } catch (err) {
      reportAgentUpdateError(set, 'updateAgentModel', err)
    }
  },

  async updateAgentName(index, name) {
    set({ agentsError: null })
    try {
      const updated = await apiUpdateAgentName(index, { name })
      set((s) => ({ agents: s.agents.map((a, i) => (i === index ? updated : a)) }))
    } catch (err) {
      reportAgentUpdateError(set, 'updateAgentName', err)
    }
  },

  async randomizeAgentName(index) {
    set({ agentsError: null })
    try {
      const updated = await apiRandomizeAgentName(index)
      set((s) => ({ agents: s.agents.map((a, i) => (i === index ? updated : a)) }))
    } catch (err) {
      reportAgentUpdateError(set, 'randomizeAgentName', err)
    }
  },

  async updateAgentPersonality(index, preset) {
    set({ agentsError: null })
    try {
      const updated = await apiUpdateAgentPersonality(index, { personality_preset: preset })
      set((s) => ({ agents: s.agents.map((a, i) => (i === index ? updated : a)) }))
    } catch (err) {
      reportAgentUpdateError(set, 'updateAgentPersonality', err)
    }
  },

  async fetchPersonalityPresets() {
    set({ personalityPresetsLoading: true, personalityPresetsError: null })
    try {
      const presets = await listPersonalityPresets()
      set({ personalityPresets: [...presets], personalityPresetsLoading: false })
    } catch (err) {
      log.error('fetchPersonalityPresets failed:', getErrorMessage(err))
      set({
        personalityPresetsError: getErrorMessage(err),
        personalityPresetsLoading: false,
      })
    }
  },
})
