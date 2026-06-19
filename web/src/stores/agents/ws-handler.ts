import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { isObject } from '@/utils/type-guards'
import { sanitizeWsEnum, sanitizeWsString } from '@/utils/ws-sanitize'
import type { WsEvent } from '@/api/types'
import type { AgentRuntimeStatus } from '@/utils/agent-status'
import type { AgentsSet } from './types'

const log = createLogger('agents')

const AGENT_RUNTIME_STATUS_VALUES = [
  'active',
  'idle',
  'error',
  'offline',
] as const satisfies readonly AgentRuntimeStatus[]

interface AgentStatusFields {
  sanitizedAgentId: string
  status: AgentRuntimeStatus
}

function extractAgentStatusFields(
  payload: Record<string, unknown>,
): AgentStatusFields | null {
  // Run the wire agent_id through the canonical WS sanitizer so it
  // can't carry control/bidi chars into ``runtimeStatuses`` as a key
  // (a malformed frame would otherwise create an unusable map entry).
  const rawAgentId = payload['agent_id']
  const sanitizedAgentId = typeof rawAgentId === 'string'
    ? sanitizeWsString(rawAgentId)
    : undefined
  if (!sanitizedAgentId) {
    log.warn('agent.status_changed payload missing required agent_id', {
      hasAgentId: typeof rawAgentId === 'string',
    })
    return null
  }
  if (sanitizedAgentId !== rawAgentId) {
    log.warn(
      'agent.status_changed id mutated during sanitization, skipping',
      { agent_id: sanitizeForLog(rawAgentId) },
    )
    return null
  }
  const status = sanitizeWsEnum<AgentRuntimeStatus>(
    payload['to_status'],
    AGENT_RUNTIME_STATUS_VALUES,
    'offline',
    { field: 'agent.status_changed.to_status' },
  )
  return { sanitizedAgentId, status }
}

function handleStatusChanged(
  set: AgentsSet,
  payload: Record<string, unknown>,
): void {
  const fields = extractAgentStatusFields(payload)
  if (!fields) return
  set((state) => ({
    runtimeStatuses: {
      ...state.runtimeStatuses,
      [fields.sanitizedAgentId]: fields.status,
    },
  }))
}

function updateFromWsEventImpl(set: AgentsSet, event: WsEvent): void {
  if (!isObject(event.payload)) {
    log.warn('WS event dropped: payload is not an object', {
      event_type: sanitizeForLog(event.event_type),
    })
    return
  }
  if (event.event_type === 'agent.status_changed') {
    handleStatusChanged(set, event.payload)
    return
  }
  // personality.trimmed is dispatched to the notifications pipeline
  // (useNotificationsStore.handleWsEvent); nothing to do on the agents store.
  if (event.event_type === 'personality.trimmed') return
  log.debug('WS event ignored: unhandled event_type', {
    event_type: sanitizeForLog(event.event_type),
  })
}

export function createWsHandler(set: AgentsSet) {
  return {
    updateRuntimeStatus: (
      agentId: string,
      status: AgentRuntimeStatus,
    ) => {
      set((state) => ({
        runtimeStatuses: { ...state.runtimeStatuses, [agentId]: status },
      }))
    },
    updateFromWsEvent: (event: WsEvent) =>
      updateFromWsEventImpl(set, event),
  }
}
