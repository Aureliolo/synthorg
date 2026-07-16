import { create, type StoreApi } from 'zustand'

import {
  getEvolutionAxisStats,
  getEvolutionSummary,
  getMetaConfig,
  getSignals,
  listABTests,
  listProposals,
  listRecentAlerts,
  postChat,
  postChatAct,
  postChatGroup,
  postChatPropose,
  type AbTestRecord,
  type AlertSummary,
  type ChatResponse,
  type ChatScope,
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
import { getErrorDetail, getErrorMessage, isAbortError, unavailableMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('meta')

type MetaSet = StoreApi<MetaState>['setState']

const FEATURE_UNAVAILABLE_TITLE = 'Conversational mode unavailable'

/**
 * Fail-closed 503 copy for a signals fetch, shared with the meta-analytics
 * page (`pages/meta/useMetaAnalyticsData.ts`) so both surfaces render the
 * same "signals not enabled for this deployment" guidance.
 */
export const SIGNALS_UNAVAILABLE_MESSAGE =
  'Signal reporting is not enabled for this deployment. Ask your administrator to enable it.'

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
  if (getErrorDetail(err)?.error_code === ErrorCode.SERVICE_UNAVAILABLE) {
    return {
      title: FEATURE_UNAVAILABLE_TITLE,
      description: unavailableMessage(
        err,
        'This conversational mode is not enabled. Ask your administrator to enable it.',
      ),
    }
  }
  return { title: fallbackTitle, description: getErrorMessage(err) }
}

async function runProposeConversation(
  set: MetaSet,
  message: string,
  conversationId?: string,
  idempotencyKey?: string,
  signal?: AbortSignal,
): Promise<ConversationalProposeResponse | null> {
  set({ proposeLoading: true })
  try {
    return await postChatPropose(message, conversationId, undefined, idempotencyKey, signal)
  } catch (err) {
    // A deliberate operator abort is not a failure: no error toast, no
    // log.error. The caller sees the null sentinel and renders a cancelled
    // state; the server still completes and parks any work idempotently.
    if (isAbortError(err)) {
      log.debug('Propose request cancelled by user')
      return null
    }
    const { title, description } = describeConversationalError(
      err,
      'Propose request failed',
    )
    log.error('Propose request failed', sanitizeForLog(err))
    // Surface via toast + the null sentinel only; the shared ``error`` slice
    // belongs to the data-fetch/config reads, so a chat failure must not
    // leak into it.
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
  idempotencyKey?: string,
): Promise<GroupConverseResult | null> {
  set({ groupChatLoading: true })
  try {
    return await postChatGroup(message, agentIds, conversationId, idempotencyKey)
  } catch (err) {
    const { title, description } = describeConversationalError(
      err,
      'Group chat request failed',
    )
    log.error('Group chat request failed', sanitizeForLog(err))
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
  idempotencyKey?: string,
): Promise<ConversationalActResult | null> {
  set({ actionLoading: true })
  try {
    return await postChatAct(instruction, agent, conversationId, idempotencyKey)
  } catch (err) {
    const { title, description } = describeConversationalError(
      err,
      'Direct action request failed',
    )
    log.error('Direct action request failed', sanitizeForLog(err))
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

async function runFetchConfig(set: MetaSet): Promise<void> {
  set({ error: null })
  try {
    set({ config: await getMetaConfig() })
  } catch (err) {
    log.error('Failed to fetch meta config', sanitizeForLog(err))
    set({ error: getErrorMessage(err) })
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

async function runFetchAlerts(set: MetaSet): Promise<void> {
  set({ error: null })
  try {
    // The chat scope picker only needs a reasonably-sized recent set,
    // not the full alert history -- bounded single page, not paginateAll.
    set({ alerts: await listRecentAlerts() })
  } catch (err) {
    log.error('Failed to fetch alerts', sanitizeForLog(err))
    set({ error: getErrorMessage(err) })
  }
}

async function runFetchSignals(set: MetaSet): Promise<void> {
  set({ error: null })
  try {
    set({ signals: await getSignals() })
  } catch (err) {
    log.error('Failed to fetch signals', sanitizeForLog(err))
    set({ error: unavailableMessage(err, SIGNALS_UNAVAILABLE_MESSAGE) })
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
  scope?: ChatScope,
  idempotencyKey?: string,
): Promise<ChatResponse | null> {
  set({ chatLoading: true })
  try {
    return await postChat(question, scope, idempotencyKey)
  } catch (err) {
    const msg = getErrorMessage(err)
    log.error('Chat request failed', sanitizeForLog(err))
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
  alerts: readonly AlertSummary[]
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
  /** Light config-only fetch for surfaces that gate on flags alone. */
  fetchConfig: () => Promise<void>
  fetchProposals: () => Promise<void>
  fetchAlerts: () => Promise<void>
  fetchSignals: () => Promise<void>
  fetchActiveAgents: () => Promise<void>
  sendChat: (
    question: string,
    scope?: ChatScope,
    idempotencyKey?: string,
  ) => Promise<ChatResponse | null>
  proposeConversation: (
    message: string,
    conversationId?: string,
    idempotencyKey?: string,
    signal?: AbortSignal,
  ) => Promise<ConversationalProposeResponse | null>
  converseGroup: (
    message: string,
    agentIds: readonly string[],
    conversationId?: string,
    idempotencyKey?: string,
  ) => Promise<GroupConverseResult | null>
  runAction: (
    instruction: string,
    agent: string,
    conversationId?: string,
    idempotencyKey?: string,
  ) => Promise<ConversationalActResult | null>
}

export const useMetaStore = create<MetaState>((set) => ({
  config: null,
  proposals: [],
  alerts: [],
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
  fetchConfig: () => runFetchConfig(set),
  fetchProposals: () => runFetchProposals(set),
  fetchAlerts: () => runFetchAlerts(set),
  fetchSignals: () => runFetchSignals(set),
  fetchActiveAgents: () => runFetchActiveAgents(set),
  sendChat: (question: string, scope?: ChatScope, idempotencyKey?: string) =>
    runSendChat(set, question, scope, idempotencyKey),
  proposeConversation: (
    message: string,
    conversationId?: string,
    idempotencyKey?: string,
    signal?: AbortSignal,
  ) => runProposeConversation(set, message, conversationId, idempotencyKey, signal),
  converseGroup: (
    message: string,
    agentIds: readonly string[],
    conversationId?: string,
    idempotencyKey?: string,
  ) => runConverseGroup(set, message, agentIds, conversationId, idempotencyKey),
  runAction: (
    instruction: string,
    agent: string,
    conversationId?: string,
    idempotencyKey?: string,
  ) => runAct(set, instruction, agent, conversationId, idempotencyKey),
}))
