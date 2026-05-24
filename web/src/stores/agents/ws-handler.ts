import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { isObject } from '@/utils/type-guards'
import { sanitizeWsString } from '@/stores/notifications'
import type { WsEvent } from '@/api/types/websocket'
import type { AgentRuntimeStatus } from '@/lib/utils'
import type { AgentsSet } from './types'

const log = createLogger('agents')

const VALID_RUNTIME_STATUSES: ReadonlySet<string> = new Set([
  'active',
  'idle',
  'error',
  'offline',
])

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
  const rawAgentId = payload.agent_id
  const sanitizedAgentId = typeof rawAgentId === 'string'
    ? sanitizeWsString(rawAgentId)
    : undefined
  const status = payload.status
  if (!sanitizedAgentId || typeof status !== 'string' || !status.trim()) {
    log.warn('agent.status_changed payload missing required fields', {
      hasAgentId: typeof rawAgentId === 'string',
      hasStatus: typeof status === 'string',
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
  if (!VALID_RUNTIME_STATUSES.has(status)) {
    log.warn('agent.status_changed received unknown status', {
      status: sanitizeForLog(status),
      knownStatuses: [...VALID_RUNTIME_STATUSES],
    })
    return null
  }
  return { sanitizedAgentId, status: status as AgentRuntimeStatus }
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
  // personality.trimmed is now handled by the unified notification
  // pipeline in useNotificationsStore.handleWsEvent (see #1078).
  if (event.event_type === 'personality.trimmed') return
  log.debug('WS event ignored: unhandled event_type', {
    event_type: sanitizeForLog(event?.event_type),
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
