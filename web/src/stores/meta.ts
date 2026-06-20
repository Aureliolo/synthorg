import { create, type StoreApi } from 'zustand'

import {
  getEvolutionAxisStats,
  getEvolutionSummary,
  getMetaConfig,
  getSignals,
  listABTests,
  listProposals,
  postChat,
  postChatAct,
  postChatGroup,
  postChatPropose,
  type AbTestRecord,
  type ChatResponse,
  type ConversationalProposeResponse,
  type EvolutionAxisStat,
  type EvolutionSummary,
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
import { ErrorCode } from '@/api/types/errors'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getErrorDetail, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('meta')

type MetaSet = StoreApi<MetaState>['setState']

const FEATURE_UNAVAILABLE_TITLE = 'Conversational mode unavailable'

/**
 * Build the toast title + description for a conversational action failure.
 *
 * A SERVICE_UNAVAILABLE (503) from these endpoints is the deliberate
 * fail-closed state (the mode is disabled, or direct-MCP acting lacks
 * security governance), not a transient outage, so it gets a distinct
 * title and surfaces the backend's specific reason rather than the
 * generic "try again" copy.
 */
function describeConversationalError(
  err: unknown,
  fallbackTitle: string,
): { title: string; description: string } {
  const detail = getErrorDetail(err)
  if (detail?.error_code === ErrorCode.SERVICE_UNAVAILABLE) {
    return {
      title: FEATURE_UNAVAILABLE_TITLE,
      description:
        detail.detail ||
        'This conversational mode is not enabled. Ask your administrator to enable it.',
    }
  }
  return { title: fallbackTitle, description: getErrorMessage(err) }
}

async function runProposeConversation(
  set: MetaSet,
  message: string,
  conversationId?: string,
): Promise<ConversationalProposeResponse | null> {
  set({ proposeLoading: true, error: null })
  try {
    return await postChatPropose(message, conversationId)
  } catch (err) {
    const { title, description } = describeConversationalError(
      err,
      'Propose request failed',
    )
    log.error('Propose request failed', sanitizeForLog(err))
    set({ error: description })
    useToastStore.getState().add({ variant: 'error', title, description })
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
    const { title, description } = describeConversationalError(
      err,
      'Group chat request failed',
    )
    log.error('Group chat request failed', sanitizeForLog(err))
    set({ error: description })
    useToastStore.getState().add({ variant: 'error', title, description })
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
    const { title, description } = describeConversationalError(
      err,
      'Direct action request failed',
    )
    log.error('Direct action request failed', sanitizeForLog(err))
    set({ error: description })
    useToastStore.getState().add({ variant: 'error', title, description })
    return null
  } finally {
    set({ actionLoading: false })
  }
}

type FetchAllResults = readonly [
  PromiseSettledResult<Awaited<ReturnType<typeof getMetaConfig>>>,
  PromiseSettledResult<Awaited<ReturnType<typeof listProposals>>>,
  PromiseSettledResult<Awaited<ReturnType<typeof listABTests>>>,
  PromiseSettledResult<Awaited<ReturnType<typeof getSignals>>>,
  PromiseSettledResult<Awaited<ReturnType<typeof getEvolutionSummary>>>,
  PromiseSettledResult<Awaited<ReturnType<typeof getEvolutionAxisStats>>>,
]

// Map the settled results to a partial update carrying ONLY the fields that
// resolved, so a failed endpoint leaves its prior value untouched instead of
// being wiped. Extracted so runFetchAll stays under the complexity cap.
function buildFetchAllUpdate(results: FetchAllResults): Partial<MetaState> {
  const [config, proposals, abTests, signals, evolutionSummary, evolutionAxes] = results
  const update: Partial<MetaState> = {}
  if (config.status === 'fulfilled') update.config = config.value
  if (proposals.status === 'fulfilled') update.proposals = proposals.value
  if (abTests.status === 'fulfilled') update.abTests = abTests.value
  if (signals.status === 'fulfilled') update.signals = signals.value
  if (evolutionSummary.status === 'fulfilled') {
    update.evolutionSummary = evolutionSummary.value
  }
  if (evolutionAxes.status === 'fulfilled') {
    update.evolutionAxes = evolutionAxes.value
  }
  return update
}

async function runFetchAll(set: MetaSet): Promise<void> {
  set({ loading: true, error: null })
  // allSettled (not all): a single failing endpoint must not wipe the data
  // the other five returned. Only successfully-fetched fields are updated;
  // failed ones keep their prior value and the first error is surfaced.
  const results = await Promise.allSettled([
    getMetaConfig(),
    listProposals(),
    listABTests(),
    getSignals(),
    getEvolutionSummary(),
    getEvolutionAxisStats(),
  ])
  const failure = results.find(
    (r): r is PromiseRejectedResult => r.status === 'rejected',
  )
  if (failure) {
    log.error('Failed to fetch some meta data', sanitizeForLog(failure.reason))
  }
  set({
    ...buildFetchAllUpdate(results),
    error: failure ? getErrorMessage(failure.reason) : null,
    loading: false,
  })
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
  abTests: readonly AbTestRecord[]
  evolutionSummary: EvolutionSummary | null
  evolutionAxes: readonly EvolutionAxisStat[]
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
  evolutionSummary: null,
  evolutionAxes: [],
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
