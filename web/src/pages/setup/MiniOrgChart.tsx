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
  rank: number
}

// Seniority rank per agent level; a department inherits the rank of its most
// senior agent, used as the leadership tiebreak when no department is named
// for leadership.
const LEVEL_RANK: Record<string, number> = {
  c_suite: 8,
  vp: 7,
  director: 6,
  principal: 5,
  lead: 4,
  senior: 3,
  mid: 2,
  junior: 1,
}

// Department names that denote the leadership tier. Matched first (before the
// level tiebreak) because a head-role exec is often materialised with a
// generic ``mid`` level, which would otherwise let a department with a senior
// IC outrank the executive box.
const LEADERSHIP_DEPTS = new Set([
  'executive',
  'leadership',
  'exec',
  'c_suite',
  'management',
])

function humanizeDept(dept: string): string {
  if (!dept) return 'Unassigned'
  return dept.replaceAll('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function buildDepartments(agents: readonly SetupAgentSummary[]): Department[] {
  const byDept = new Map<string, Department>()
  for (const agent of agents) {
    let dept = byDept.get(agent.department)
    if (!dept) {
      dept = {
        name: agent.department,
        label: humanizeDept(agent.department),
        agents: [],
        rank: 0,
      }
      byDept.set(agent.department, dept)
    }
    dept.agents.push(agent)
    const rank = agent.level ? (LEVEL_RANK[agent.level] ?? 0) : 0
    if (rank > dept.rank) dept.rank = rank
  }
  return [...byDept.values()]
}

/**
 * Split departments into the single leadership department (the one whose most
 * senior agent outranks every other department) and the rest that report to
 * it. Returns a null lead when no department clearly sits above the others, so
 * the caller falls back to a flat row instead of inventing a hierarchy.
 */
function splitLeadership(depts: Department[]): {
  lead: Department | null
  rest: Department[]
} {
  if (depts.length < 2) return { lead: null, rest: depts }
  const named = depts.find((d) => LEADERSHIP_DEPTS.has(d.name.toLowerCase()))
  if (named) {
    return { lead: named, rest: depts.filter((d) => d !== named) }
  }
  const sorted = [...depts].sort((a, b) => b.rank - a.rank)
  const [top, second] = sorted
  if (top && second && top.rank > second.rank) {
    return { lead: top, rest: sorted.slice(1) }
  }
  return { lead: null, rest: depts }
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

function DepartmentBox({
  dept,
  highlight = false,
}: {
  dept: Department
  highlight?: boolean
}) {
  return (
    <div
      className={cn(
        'flex min-w-[12rem] flex-col gap-4 rounded-xl border bg-card p-card shadow-sm',
        highlight ? 'border-primary/40 bg-primary/5' : 'border-border',
      )}
    >
      <div className="flex items-center gap-2">
        <span
          className={cn(
            'text-[11px] font-semibold uppercase tracking-wider',
            highlight ? 'text-primary' : 'text-foreground',
          )}
        >
          {dept.label}
        </span>
        <span className="rounded-full bg-muted px-1.5 text-[10px] font-medium text-text-muted">
          {dept.agents.length}
        </span>
      </div>
      <div className="flex flex-wrap gap-4">
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
 * One subordinate department under the leadership box. Draws the connector
 * stub: a horizontal bus segment (half-width on the outer columns so the bus
 * starts/ends at the first/last branch) plus a vertical branch down to the
 * box. A lone child draws only the vertical branch.
 */
function ChildColumn({
  dept,
  first,
  last,
  single,
}: {
  dept: Department
  first: boolean
  last: boolean
  single: boolean
}) {
  return (
    <div className="flex flex-1 flex-col items-center">
      <div className="relative h-7 w-full" aria-hidden>
        {!single && (
          <div
            className={cn(
              'absolute top-0 h-px bg-border',
              first ? 'left-1/2 right-0' : last ? 'left-0 right-1/2' : 'left-0 right-0',
            )}
          />
        )}
        <div className="absolute left-1/2 top-0 h-7 w-px -translate-x-1/2 bg-border" />
      </div>
      <div className="w-full px-2">
        <DepartmentBox dept={dept} />
      </div>
    </div>
  )
}

/**
 * Compact org preview: the leadership department on top, the departments that
 * report to it on a connected row below, each box holding its agents as little
 * avatar rounds with a headcount. When no department clearly leads, falls back
 * to a flat wrapped row of boxes rather than fabricating reporting lines.
 */
export function MiniOrgChart({ agents, className }: MiniOrgChartProps) {
  const departments = useMemo(() => buildDepartments(agents), [agents])
  const { lead, rest } = useMemo(() => splitLeadership(departments), [departments])
  if (departments.length === 0) return null

  if (!lead) {
    return (
      <div className={cn('flex flex-wrap gap-3', className)}>
        {rest.map((dept) => (
          <DepartmentBox key={dept.name} dept={dept} />
        ))}
      </div>
    )
  }

  return (
    <div className={cn('flex flex-col items-center py-4', className)}>
      <DepartmentBox dept={lead} highlight />
      <div className="h-7 w-px bg-border" aria-hidden />
      <div className="flex w-full items-start justify-center overflow-x-auto pb-2">
        {rest.map((dept, index) => (
          <ChildColumn
            key={dept.name}
            dept={dept}
            first={index === 0}
            last={index === rest.length - 1}
            single={rest.length === 1}
          />
        ))}
      </div>
    </div>
  )
}
