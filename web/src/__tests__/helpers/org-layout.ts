import type { Node } from '@xyflow/react'
import type { DashboardAgentConfig } from '@/api/types/agents'
import type { CompanyConfig, DashboardDepartment, TeamConfig } from '@/api/types/org'
import { buildOrgTree, type OwnerInfo } from '@/pages/org/build-org-tree'
import { applyDagreLayout } from '@/pages/org/layout'
import { makeAgent, makeDepartment } from './factories'

/** One department's worth of org data in the operator's own order. */
export interface DeptSpec {
  /** Department key; ``executive`` holds the CEO and becomes the root box. */
  readonly name: string
  /** Member names in the operator's order; the first one heads the department. */
  readonly members: readonly string[]
  /** Teams within the department, in the operator's order. */
  readonly teams?: readonly { readonly name: string; readonly members: readonly string[] }[]
}

export const OWNERS: readonly OwnerInfo[] = [{ id: 'owner-1', displayName: 'Owner' }]

export const OWNER_NODE_ID = 'owner-owner-1'
export const ROOT_DEPT_NODE_ID = 'dept-executive'

function headRoleOf(deptName: string): string {
  return deptName === 'executive' ? 'CEO' : `Head of ${deptName}`
}

function agentsOf(spec: DeptSpec): DashboardAgentConfig[] {
  return spec.members.map((name, index) =>
    makeAgent(name, {
      department: spec.name,
      role: index === 0 ? headRoleOf(spec.name) : 'Developer',
    }),
  )
}

function departmentOf(spec: DeptSpec): DashboardDepartment {
  // The first listed member leads the team, mirroring how the operator's
  // team roster reads in the Org Edit page.
  const teams: TeamConfig[] = (spec.teams ?? []).map((team) => ({
    name: team.name,
    lead: team.members[0] ?? '',
    members: [...team.members],
  }))
  return makeDepartment(spec.name, { head: headRoleOf(spec.name), teams })
}

export function orgConfig(specs: readonly DeptSpec[]): CompanyConfig {
  return {
    company_name: 'Test Corp',
    agents: specs.flatMap(agentsOf),
    departments: specs.map(departmentOf),
  }
}

export function layoutOf(config: CompanyConfig): Node[] {
  const tree = buildOrgTree({
    config,
    runtimeStatuses: {},
    departmentHealths: [],
    owners: OWNERS,
  })
  return applyDagreLayout(tree.nodes, tree.edges)
}

export function positionsOf(
  nodes: readonly Node[],
): Record<string, { x: number; y: number }> {
  const out: Record<string, { x: number; y: number }> = {}
  for (const node of nodes) {
    out[node.id] = { x: node.position.x, y: node.position.y }
  }
  return out
}

/** The given ids sorted left to right by their laid-out x. */
export function leftToRight(nodes: readonly Node[], ids: readonly string[]): string[] {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  return [...ids]
    .filter((id) => byId.has(id))
    .sort((a, b) => byId.get(a)!.position.x - byId.get(b)!.position.x)
}

/** The given ids sorted top to bottom by their laid-out y. */
export function topToBottom(nodes: readonly Node[], ids: readonly string[]): string[] {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  return [...ids]
    .filter((id) => byId.has(id))
    .sort((a, b) => byId.get(a)!.position.y - byId.get(b)!.position.y)
}

export function agentIds(names: readonly string[]): string[] {
  return names.map((name) => `agent-${name}`)
}

export function nodeById(nodes: readonly Node[], id: string): Node {
  const found = nodes.find((n) => n.id === id)
  if (!found) throw new Error(`no laid-out node with id ${id}`)
  return found
}
