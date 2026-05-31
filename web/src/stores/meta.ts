import { create, type StoreApi } from 'zustand'

import {
  getMetaConfig,
  getSignals,
  listABTests,
  listProposals,
  postChat,
  postChatPropose,
  type ABTestSummary,
  type ChatResponse,
  type ConversationalProposeResponse,
  type MetaConfig,
  type ProposalSummary,
  type SignalsResponse,
} from '@/api/endpoints/meta'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('meta')

type MetaSet = StoreApi<MetaState>['setState']

async function runProposeConversation(
  set: MetaSet,
  message: string,
  conversationId?: string,
): Promise<ConversationalProposeResponse | null> {
  set({ proposeLoading: true, error: null })
  try {
    return await postChatPropose(message, conversationId)
  } catch (err) {
    const msg = getErrorMessage(err)
    log.error('Propose request failed', sanitizeForLog(err))
    set({ error: msg })
    useToastStore.getState().add({
      variant: 'error',
      title: 'Propose request failed',
      description: msg,
    })
    return null
  } finally {
    set({ proposeLoading: false })
  }
}

interface MetaState {
  // Data
  config: MetaConfig | null
  proposals: readonly ProposalSummary[]
  abTests: readonly ABTestSummary[]
  signals: SignalsResponse | null

  // UI state
  loading: boolean
  error: string | null
  chatLoading: boolean
  proposeLoading: boolean

  // Actions
  fetchAll: () => Promise<void>
  fetchProposals: () => Promise<void>
  fetchSignals: () => Promise<void>
  sendChat: (question: string) => Promise<ChatResponse | null>
  proposeConversation: (
    message: string,
    conversationId?: string,
  ) => Promise<ConversationalProposeResponse | null>
}

export const useMetaStore = create<MetaState>((set) => ({
  config: null,
  proposals: [],
  abTests: [],
  signals: null,
  loading: false,
  error: null,
  chatLoading: false,
  proposeLoading: false,

  fetchAll: async () => {
    set({ loading: true, error: null })
    try {
      const [config, proposals, abTests, signals] = await Promise.all([
        getMetaConfig(),
        listProposals(),
        listABTests(),
        getSignals(),
      ])
      set({ config, proposals, abTests, signals, loading: false })
    } catch (err) {
      log.error('Failed to fetch meta data', sanitizeForLog(err))
      set({
        config: null,
        proposals: [],
        abTests: [],
        signals: null,
        error: getErrorMessage(err),
        loading: false,
      })
    }
  },

  fetchProposals: async () => {
    set({ error: null })
    try {
      const proposals = await listProposals()
      set({ proposals })
    } catch (err) {
      log.error('Failed to fetch proposals', sanitizeForLog(err))
      set({ error: getErrorMessage(err) })
    }
  },

  fetchSignals: async () => {
    set({ error: null })
    try {
      const signals = await getSignals()
      set({ signals })
    } catch (err) {
      log.error('Failed to fetch signals', sanitizeForLog(err))
      set({ error: getErrorMessage(err) })
    }
  },

  sendChat: async (question: string) => {
    set({ chatLoading: true, error: null })
    try {
      const response = await postChat(question)
      return response
    } catch (err) {
      const msg = getErrorMessage(err)
      log.error('Chat request failed', sanitizeForLog(err))
      set({ error: msg })
      useToastStore.getState().add({
        variant: 'error',
        title: 'Chat request failed',
        description: msg,
      })
      return null
    } finally {
      set({ chatLoading: false })
    }
  },

  proposeConversation: (message: string, conversationId?: string) =>
    runProposeConversation(set, message, conversationId),
}))
