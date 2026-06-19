import type { AgentStatus } from '@/api/types/enums'
import type { AgentRuntimeStatus } from '@/utils/agent-status'

/**
 * Map HR (administrative) agent status to an initial runtime status.
 * Both 'active' and 'onboarding' map to 'idle' because actual runtime
 * activity is determined by WebSocket events, not administrative status.
 */
export function mapHrToRuntime(hrStatus: AgentStatus): AgentRuntimeStatus {
  switch (hrStatus) {
    case 'terminated':
    case 'on_leave':
      return 'offline'
    case 'onboarding':
    case 'active':
      return 'idle'
  }
}

/**
 * Resolve the effective runtime status for an agent.
 * WebSocket-pushed runtime status takes precedence over the HR-derived default.
 */
export function resolveRuntimeStatus(
  agentId: string,
  hrStatus: AgentStatus,
  runtimeMap: Record<string, AgentRuntimeStatus>,
): AgentRuntimeStatus {
  return runtimeMap[agentId] ?? mapHrToRuntime(hrStatus)
}
