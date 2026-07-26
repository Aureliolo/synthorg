import { AgentCard } from '@/components/ui/agent-card'
import {
  agentCapabilities,
  agentCapabilitiesUnavailable,
  agentCapabilitiesUnverified,
  agentModelBindingUnresolved,
  agentModelId,
  agentPersonalityLabel,
  agentToolCallsFailed,
  agentTraits,
  toRuntimeStatus,
} from '@/utils/agents'
import type { AgentConfig } from '@/api/types/agents'

export interface AgentConfigCardProps {
  agent: AgentConfig
  /** Pre-formatted relative timestamp (e.g. hiring date), if shown. */
  timestamp?: string | undefined
  /** ISO source of ``timestamp`` for the machine-readable attribute. */
  timestampIso?: string | undefined
  className?: string | undefined
}

/**
 * Single mapping from a domain ``AgentConfig`` onto the shared
 * ``AgentCard``, so every roster surface (workspace grid, org-edit
 * board, drag overlays) renders identical agent information.
 */
export function AgentConfigCard({
  agent,
  timestamp,
  timestampIso,
  className,
}: AgentConfigCardProps) {
  return (
    <AgentCard
      name={agent.name}
      role={agent.role}
      department={agent.department}
      status={toRuntimeStatus(agent.status ?? 'active')}
      model={agentModelId(agent)}
      tier={agent.tier}
      personality={agentPersonalityLabel(agent)}
      traits={agentTraits(agent)}
      capabilities={agentCapabilities(agent)}
      toolCallsFailed={agentToolCallsFailed(agent)}
      capabilitiesUnverified={agentCapabilitiesUnverified(agent)}
      modelBindingUnresolved={agentModelBindingUnresolved(agent)}
      capabilitiesUnavailable={agentCapabilitiesUnavailable(agent)}
      timestamp={timestamp}
      timestampIso={timestampIso}
      className={className}
    />
  )
}
