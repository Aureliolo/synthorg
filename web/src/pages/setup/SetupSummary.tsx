import { SectionCard } from '@/components/ui/section-card'
import { MetricCard } from '@/components/ui/metric-card'
import { StatPill } from '@/components/ui/stat-pill'
import { StatusBadge } from '@/components/ui/status-badge'
import { Avatar } from '@/components/ui/avatar'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import type { ProviderConfig } from '@/api/types/providers'
import type { SetupAgentSummary, SetupCompanyResponse } from '@/api/types/setup'
import { getProviderStatus } from '@/utils/provider-status'
import { formatModelTier } from '@/utils/model-tiers'
import { Building2, Users, Server } from 'lucide-react'

function SetupAgentRow({ agent }: { agent: SetupAgentSummary }) {
  return (
    <div className="flex items-center gap-3 rounded-md border border-border p-card-snug">
      <Avatar name={agent.name} size="sm" />
      <div className="flex-1">
        <span className="text-sm font-medium text-foreground">{agent.name}</span>
        <span className="ml-2 text-xs text-muted-foreground">{agent.role}</span>
      </div>
      <div className="flex items-center gap-2">
        <StatPill label="Dept" value={agent.department} />
        <StatPill label="Tier" value={formatModelTier(agent.tier)} />
      </div>
    </div>
  )
}

export interface SetupSummaryProps {
  companyResponse: SetupCompanyResponse
  agents: readonly SetupAgentSummary[]
  providers: Readonly<Record<string, ProviderConfig>>
  currency: string
}

export function SetupSummary({
  companyResponse,
  agents,
  providers,
  currency,
}: SetupSummaryProps) {
  return (
    <div className="space-y-section-gap">
      {/* Company details */}
      <SectionCard title="Company Details" icon={Building2}>
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Name:</span>
            <span className="text-sm font-medium text-foreground">{companyResponse.company_name}</span>
          </div>
          {companyResponse.template_applied && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">Template:</span>
              <StatPill value={companyResponse.template_applied} />
            </div>
          )}
          {companyResponse.description && (
            <p className="text-sm text-muted-foreground">{companyResponse.description}</p>
          )}
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Currency:</span>
            <span className="text-sm font-medium text-foreground">{currency}</span>
          </div>
        </div>
      </SectionCard>

      {/* Metrics row */}
      <StaggerGroup className="grid grid-cols-3 gap-grid-gap max-sm:grid-cols-1">
        <StaggerItem>
          <MetricCard label="Departments" value={companyResponse.department_count} />
        </StaggerItem>
        <StaggerItem>
          <MetricCard label="Agents" value={agents.length} />
        </StaggerItem>
        <StaggerItem>
          <MetricCard label="Providers" value={Object.keys(providers).length} />
        </StaggerItem>
      </StaggerGroup>

      {/* Agent roster */}
      <SectionCard title="Agent Roster" icon={Users}>
        <div className="max-h-80 space-y-2 overflow-y-auto">
          {agents.map((agent, index) => (
            // eslint-disable-next-line @eslint-react/no-array-index-key -- setup agents can share names; index as tiebreaker
            <SetupAgentRow key={`${agent.name}-${index}`} agent={agent} />
          ))}
        </div>
      </SectionCard>

      {/* Providers */}
      <SectionCard title="Connected Providers" icon={Server}>
        <div className="space-y-2">
          {Object.entries(providers).map(([name, config]) => (
            <div key={name} className="flex items-center justify-between rounded-md border border-border p-card-snug">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-foreground">{name}</span>
                <span className="text-xs text-muted-foreground">{config.models.length} models</span>
              </div>
              <StatusBadge status={getProviderStatus(config)} label />
            </div>
          ))}
          {Object.keys(providers).length === 0 && (
            <p className="text-sm text-muted-foreground">No providers configured yet.</p>
          )}
        </div>
      </SectionCard>
    </div>
  )
}
