import { create, type StoreApi } from 'zustand'

import {
  getMetaConfig,
  getSignals,
  listABTests,
  listProposals,
  postChat,
  postChatAct,
  postChatGroup,
  postChatPropose,
  type ABTestSummary,
  type ChatResponse,
  type ConversationalProposeResponse,
  type MetaConfig,
  type ProposalSummary,
  type SignalsResponse,
} from '@/api/endpoints/meta'
import { listActiveAgents } from '@/api/endpoints/agents'
import type {
  ActiveAgentSummary,
  ConversationalActResult,
  GroupConverseResult,
} from '@/api/types'
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

async function runConverseGroup(
  set: MetaSet,
  message: string,
  agentIds: readonly string[],
  conversationId?: string,
): Promise<GroupConverseResult | null> {
  set({ groupChatLoading: true, error: null })
  try {
    return await postChatGroup(message, agentIds, conversationId)
  } catch (err) {
    const msg = getErrorMessage(err)
    log.error('Group chat request failed', sanitizeForLog(err))
    set({ error: msg })
    useToastStore.getState().add({
      variant: 'error',
      title: 'Group chat request failed',
      description: msg,
    })
    return null
  } finally {
    set({ groupChatLoading: false })
  }
}

async function runAct(
  set: MetaSet,
  instruction: string,
  agent: string,
  conversationId?: string,
): Promise<ConversationalActResult | null> {
  set({ actionLoading: true, error: null })
  try {
    return await postChatAct(instruction, agent, conversationId)
  } catch (err) {
    const msg = getErrorMessage(err)
    log.error('Direct action request failed', sanitizeForLog(err))
    set({ error: msg })
    useToastStore.getState().add({
      variant: 'error',
      title: 'Direct action request failed',
      description: msg,
    })
    return null
  } finally {
    set({ actionLoading: false })
  }
}

async function runFetchAll(set: MetaSet): Promise<void> {
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
}

async function runFetchProposals(set: MetaSet): Promise<void> {
  set({ error: null })
  try {
    set({ proposals: await listProposals() })
  } catch (err) {
    log.error('Failed to fetch proposals', sanitizeForLog(err))
    set({ error: getErrorMessage(err) })
  }
}

async function runFetchSignals(set: MetaSet): Promise<void> {
  set({ error: null })
  try {
    set({ signals: await getSignals() })
  } catch (err) {
    log.error('Failed to fetch signals', sanitizeForLog(err))
    set({ error: getErrorMessage(err) })
  }
}

async function runFetchActiveAgents(set: MetaSet): Promise<void> {
  set({ error: null })
  try {
    set({ activeAgents: await listActiveAgents() })
  } catch (err) {
    log.error('Failed to fetch active agents', sanitizeForLog(err))
    set({ error: getErrorMessage(err) })
  }
}

async function runSendChat(
  set: MetaSet,
  question: string,
): Promise<ChatResponse | null> {
  set({ chatLoading: true, error: null })
  try {
    return await postChat(question)
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
}

interface MetaState {
  // Data
  config: MetaConfig | null
  proposals: readonly ProposalSummary[]
  abTests: readonly ABTestSummary[]
  signals: SignalsResponse | null
  activeAgents: readonly ActiveAgentSummary[]

  // UI state
  loading: boolean
  error: string | null
  chatLoading: boolean
  proposeLoading: boolean
  groupChatLoading: boolean
  actionLoading: boolean

  // Actions
  fetchAll: () => Promise<void>
  fetchProposals: () => Promise<void>
  fetchSignals: () => Promise<void>
  fetchActiveAgents: () => Promise<void>
  sendChat: (question: string) => Promise<ChatResponse | null>
  proposeConversation: (
    message: string,
    conversationId?: string,
  ) => Promise<ConversationalProposeResponse | null>
  converseGroup: (
    message: string,
    agentIds: readonly string[],
    conversationId?: string,
  ) => Promise<GroupConverseResult | null>
  runAction: (
    instruction: string,
    agent: string,
    conversationId?: string,
  ) => Promise<ConversationalActResult | null>
}

export const useMetaStore = create<MetaState>((set) => ({
  config: null,
  proposals: [],
  abTests: [],
  signals: null,
  activeAgents: [],
  loading: false,
  error: null,
  chatLoading: false,
  proposeLoading: false,
  groupChatLoading: false,
  actionLoading: false,

  fetchAll: () => runFetchAll(set),
  fetchProposals: () => runFetchProposals(set),
  fetchSignals: () => runFetchSignals(set),
  fetchActiveAgents: () => runFetchActiveAgents(set),
  sendChat: (question: string) => runSendChat(set, question),
  proposeConversation: (message: string, conversationId?: string) =>
    runProposeConversation(set, message, conversationId),
  converseGroup: (
    message: string,
    agentIds: readonly string[],
    conversationId?: string,
  ) => runConverseGroup(set, message, agentIds, conversationId),
  runAction: (instruction: string, agent: string, conversationId?: string) =>
    runAct(set, instruction, agent, conversationId),
}))
