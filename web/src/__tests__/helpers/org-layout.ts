import type { Node } from '@xyflow/react'
import type { DashboardAgentConfig } from '@/api/types/agents'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { AgentRuntimeStatus } from '@/utils/agent-status'
import type { CompanyConfig, DashboardDepartment, TeamConfig } from '@/api/types/org'
import { buildOrgTree, type OwnerInfo } from '@/pages/org/build-org-tree'
import type { DeptAdminInfo } from '@/pages/org/build-org-tree-types'
import { applyDagreLayout, type LayoutOptions } from '@/pages/org/layout'
import { getNodeDim } from '@/pages/org/layout-shared'
import { makeAgent, makeDepartment } from './factories'

/** One team's worth of org data in the operator's own order. */
export interface TeamSpec {
  readonly name: string
  /** Member names; the first one leads the team. Empty means unstaffed. */
  readonly members: readonly string[]
  /** Set to name a lead that is not a member, to exercise an unresolved lead. */
  readonly lead?: string
}

/** One department's worth of org data in the operator's own order. */
export interface DeptSpec {
  /** Department key; ``executive`` holds the CEO and becomes the root box. */
  readonly name: string
  /** Member names in the operator's order; the first one heads the department. */
  readonly members: readonly string[]
  /** Teams within the department, in the operator's order. */
  readonly teams?: readonly TeamSpec[]
}

const DEFAULT_OWNERS: readonly OwnerInfo[] = [{ id: 'owner-1', displayName: 'Owner' }]

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
    lead: team.lead ?? team.members[0] ?? '',
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

export interface LayoutFixtureOptions {
  readonly owners?: readonly OwnerInfo[]
  readonly deptAdmins?: readonly DeptAdminInfo[]
  readonly layout?: LayoutOptions
  readonly runtimeStatuses?: Record<string, AgentRuntimeStatus>
  readonly departmentHealths?: readonly DepartmentHealth[]
}

export function layoutOf(config: CompanyConfig, options: LayoutFixtureOptions = {}): Node[] {
  const tree = buildOrgTree({
    config,
    runtimeStatuses: options.runtimeStatuses ?? {},
    departmentHealths: options.departmentHealths ?? [],
    owners: options.owners ?? DEFAULT_OWNERS,
    deptAdmins: options.deptAdmins,
  })
  return applyDagreLayout(tree.nodes, tree.edges, options.layout ?? {})
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

export function childrenOf(nodes: readonly Node[], parentId: string): Node[] {
  return nodes.filter((n) => n.parentId === parentId)
}

/** True when two nodes' boxes intersect, in whatever frame they share. */
export function overlaps(a: Node, b: Node): boolean {
  const dimA = getNodeDim(a)
  const dimB = getNodeDim(b)
  return (
    a.position.x < b.position.x + dimB.w
    && b.position.x < a.position.x + dimA.w
    && a.position.y < b.position.y + dimB.h
    && b.position.y < a.position.y + dimA.h
  )
}

/** True when a child's box sits wholly inside its parent's, in the parent's frame. */
export function fitsInside(child: Node, parent: Node): boolean {
  const { w, h } = getNodeDim(child)
  const box = getNodeDim(parent)
  return (
    child.position.x >= 0
    && child.position.y >= 0
    && child.position.x + w <= box.w
    && child.position.y + h <= box.h
  )
}

/** Every pair of the given nodes, for exhaustive overlap assertions. */
export function pairsOf<T>(items: readonly T[]): [T, T][] {
  const pairs: [T, T][] = []
  for (let i = 0; i < items.length; i++) {
    for (let j = i + 1; j < items.length; j++) pairs.push([items[i]!, items[j]!])
  }
  return pairs
}
