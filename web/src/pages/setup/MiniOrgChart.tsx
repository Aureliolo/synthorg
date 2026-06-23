import { useMemo } from 'react'
import { Avatar } from '@/components/ui/avatar'
import { cn } from '@/lib/utils'
import { seniorityRank } from '@/utils/agents'
import type { SetupAgentSummary } from '@/api/types/setup'

export interface MiniOrgChartProps {
  agents: readonly SetupAgentSummary[]
  className?: string
}

interface OrgTreeNode {
  agent: SetupAgentSummary
  isLead: boolean
  deptLabel: string | null
  children: OrgTreeNode[]
}

function humanizeDept(dept: string): string {
  return dept
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

// Role-title authority, highest first, independent of the `level` field: the
// template can leave a CEO at `level: mid`, so ranking purely on seniority
// would crown a senior IC. Role wins, seniority only tie-breaks.
const ROLE_RANKS: readonly (readonly [RegExp, number])[] = [
  [/\bceo\b|chief executive|founder/, 5],
  [/^chief|\bc[a-z]o\b/, 4],
  [/vice president|\bvp\b/, 3],
  [/director|head of/, 2],
  [/lead|principal|manager/, 1],
]

function roleRank(role: string): number {
  const r = role.toLowerCase()
  for (const [pattern, rank] of ROLE_RANKS) {
    if (pattern.test(r)) return rank
  }
  return 0
}

function headScore(agent: SetupAgentSummary): number {
  // Role dominates; seniority (0-7) only separates equal roles.
  return roleRank(agent.role) * 100 + seniorityRank(agent.level ?? null)
}

function pickHead(agents: readonly SetupAgentSummary[]): SetupAgentSummary | null {
  if (agents.length === 0) return null
  let head = agents[0]!
  for (const agent of agents) {
    if (headScore(agent) > headScore(head)) head = agent
  }
  return head
}

function groupByDept(
  agents: readonly SetupAgentSummary[],
): Map<string, SetupAgentSummary[]> {
  const byDept = new Map<string, SetupAgentSummary[]>()
  for (const agent of agents) {
    const arr = byDept.get(agent.department) ?? []
    arr.push(agent)
    byDept.set(agent.department, arr)
  }
  return byDept
}

/** The leadership department roots the tree: the one named ~"executive",
 *  else whichever department owns the highest-authority agent. */
function pickRootDept(
  byDept: Map<string, SetupAgentSummary[]>,
  companyHead: SetupAgentSummary,
): string {
  for (const dept of byDept.keys()) {
    if (dept.toLowerCase().replace(/[\s_]/g, '').includes('executive')) return dept
  }
  return companyHead.department
}

function memberLeaf(agent: SetupAgentSummary): OrgTreeNode {
  return { agent, isLead: false, deptLabel: null, children: [] }
}

/** A department head node with its remaining members as reports. */
function deptHeadNode(dept: string, members: SetupAgentSummary[]): OrgTreeNode | null {
  const head = pickHead(members)
  if (!head) return null
  return {
    agent: head,
    isLead: true,
    deptLabel: humanizeDept(dept),
    children: members.filter((m) => m !== head).map(memberLeaf),
  }
}

/**
 * Reporting tree: the company head (top role in the leadership department)
 * roots it; each other department's head reports to the company head, and a
 * department's remaining members report to their head. The leadership
 * department's other members report to the company head directly.
 */
function buildOrgTree(agents: readonly SetupAgentSummary[]): OrgTreeNode | null {
  const companyHead = pickHead(agents)
  if (!companyHead) return null
  const byDept = groupByDept(agents)
  const rootDept = pickRootDept(byDept, companyHead)
  const root = pickHead(byDept.get(rootDept) ?? agents) ?? companyHead

  const children: OrgTreeNode[] = []
  for (const [dept, members] of byDept) {
    if (dept === rootDept) {
      children.push(...members.filter((m) => m !== root).map(memberLeaf))
    } else {
      const node = deptHeadNode(dept, members)
      if (node) children.push(node)
    }
  }
  return { agent: root, isLead: true, deptLabel: humanizeDept(rootDept), children }
}

function OrgRound({ node }: { node: OrgTreeNode }) {
  return (
    <div
      className="flex w-24 flex-col items-center gap-1"
      title={`${node.agent.name} - ${node.agent.role}`}
    >
      <Avatar
        name={node.agent.name}
        size="sm"
        className={cn(node.isLead && 'ring-2 ring-accent/60')}
      />
      <span className="w-full truncate text-center text-[11px] leading-tight text-foreground">
        {node.agent.name}
      </span>
      {node.deptLabel ? (
        <span className="w-full truncate text-center text-[9px] uppercase tracking-wide text-text-muted">
          {node.deptLabel}
        </span>
      ) : (
        <span className="w-full truncate text-center text-[9px] leading-tight text-text-muted">
          {node.agent.role}
        </span>
      )}
    </div>
  )
}

/** Horizontal connector segment above a child: half-width for the first/last
 *  sibling so the segments join into one continuous bus, full-width between. */
function busClass(index: number, count: number): string {
  if (count === 1) return 'hidden'
  if (index === 0) return 'left-1/2 right-0'
  if (index === count - 1) return 'left-0 right-1/2'
  return 'inset-x-0'
}

function OrgBranch({ node }: { node: OrgTreeNode }) {
  return (
    <div className="flex flex-col items-center">
      <OrgRound node={node} />
      {node.children.length > 0 && (
        <>
          <div className="h-4 w-px bg-border" />
          <div className="flex items-start">
            {node.children.map((kid, i) => (
              <div
                // eslint-disable-next-line @eslint-react/no-array-index-key -- agents can share names; index is the stable tiebreaker
                key={`${kid.agent.name}-${i}`}
                className="relative flex flex-col items-center px-2.5 pt-4"
              >
                <span className={cn('absolute top-0 h-px bg-border', busClass(i, node.children.length))} />
                <span className="absolute left-1/2 top-0 h-4 w-px -translate-x-1/2 bg-border" />
                <OrgBranch node={kid} />
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

/**
 * Simple top-down organisation chart: the company head connects to department
 * leads, who connect to their members, with small agent rounds + department
 * labels. A lightweight, read-only alternative to the full react-flow org view.
 */
export function MiniOrgChart({ agents, className }: MiniOrgChartProps) {
  const tree = useMemo(() => buildOrgTree(agents), [agents])
  if (!tree) return null
  return (
    <div className={cn('overflow-x-auto rounded-lg border border-border bg-card p-card', className)}>
      <div className="flex min-w-fit justify-center px-2 py-2">
        <OrgBranch node={tree} />
      </div>
    </div>
  )
}
