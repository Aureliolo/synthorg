import { useMemo } from 'react'
import { Avatar } from '@/components/ui/avatar'
import { cn } from '@/lib/utils'
import type { SetupAgentSummary } from '@/api/types/setup'

export interface MiniOrgChartProps {
  agents: readonly SetupAgentSummary[]
  className?: string
}

interface Department {
  name: string
  label: string
  agents: SetupAgentSummary[]
}

function humanizeDept(dept: string): string {
  return dept.replaceAll('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function buildDepartments(agents: readonly SetupAgentSummary[]): Department[] {
  const byDept = new Map<string, Department>()
  for (const agent of agents) {
    let dept = byDept.get(agent.department)
    if (!dept) {
      dept = { name: agent.department, label: humanizeDept(agent.department), agents: [] }
      byDept.set(agent.department, dept)
    }
    dept.agents.push(agent)
  }
  return [...byDept.values()]
}

function AgentRound({ agent }: { agent: SetupAgentSummary }) {
  return (
    <div
      className="flex w-16 flex-col items-center gap-1"
      title={`${agent.name} - ${agent.role}`}
    >
      <Avatar name={agent.name} size="sm" />
      <span className="w-full truncate text-center text-[10px] leading-tight text-foreground">
        {agent.name}
      </span>
    </div>
  )
}

function DepartmentBox({ dept }: { dept: Department }) {
  return (
    <div className="flex min-w-[13rem] flex-1 flex-col gap-3 rounded-lg border border-border bg-card p-card">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground">
          {dept.label}
        </span>
        <span className="text-[11px] text-text-muted">{dept.agents.length}</span>
      </div>
      <div className="flex flex-wrap gap-3">
        {dept.agents.map((agent, index) => (
          <AgentRound
            // eslint-disable-next-line @eslint-react/no-array-index-key -- agents can share names; index is the stable tiebreaker
            key={`${agent.name}-${index}`}
            agent={agent}
          />
        ))}
      </div>
    </div>
  )
}

/**
 * Department-grouped org overview: one box per department holding its agents as
 * simple rounds. No reporting lines or lead emphasis -- a quiet structural
 * snapshot rather than a hierarchy chart.
 */
export function MiniOrgChart({ agents, className }: MiniOrgChartProps) {
  const departments = useMemo(() => buildDepartments(agents), [agents])
  if (departments.length === 0) return null
  return (
    <div className={cn('flex flex-wrap gap-3', className)}>
      {departments.map((dept) => (
        <DepartmentBox key={dept.name} dept={dept} />
      ))}
    </div>
  )
}
