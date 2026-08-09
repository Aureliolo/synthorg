import type { Edge, Node } from '@xyflow/react'
import { liftEdges, resolveDepartmentOf } from './layout-clusters'
import type { DagreParams } from './layout-graph'
import {
  type DepartmentChrome,
  type SizedUnit,
  sizeDepartment,
  sizeTeam,
} from './layout-units'

/** The dept group ids flagged as the root by build-org-tree. */
export function collectRootGroupIds(groupNodes: readonly Node[]): Set<string> {
  const rootGroupIds = new Set<string>()
  for (const group of groupNodes) {
    if (group.data['isRootDepartment'] === true) {
      rootGroupIds.add(group.id)
    }
  }
  return rootGroupIds
}

/** Every unit sized and nested, ready for the top-level frame to place. */
export interface HierarchyPlan {
  /** What the top-level frame lays out: loose leaves plus department boxes. */
  readonly topLevelNodes: Node[]
  /** Edges rewritten so both endpoints are top-level ids. */
  readonly topLevelEdges: Edge[]
  /** Department boxes with their contents, keyed by department id. */
  readonly departments: Map<string, SizedUnit>
  /** Teams with their contents, keyed by team id. */
  readonly teams: Map<string, SizedUnit>
}

export interface HierarchyArgs {
  readonly nodes: readonly Node[]
  readonly edges: readonly Edge[]
  readonly params: DagreParams
  readonly chrome: DepartmentChrome
}

function isLeaf(node: Node): boolean {
  return node.type !== 'department' && node.type !== 'team'
}

/** True when this node is already drawn inside a team's box. */
function isTeamMember(node: Node, teamIds: ReadonlySet<string>): boolean {
  return isLeaf(node) && node.parentId !== undefined && teamIds.has(node.parentId)
}

/** Lay out every team box, walking in emission order. */
function planTeams(
  args: HierarchyArgs,
  teamIds: ReadonlySet<string>,
): { teams: Map<string, SizedUnit>; teamOf: Map<string, string> } {
  const teams = new Map<string, SizedUnit>()
  const teamOf = new Map<string, string>()
  for (const team of args.nodes.filter((n) => teamIds.has(n.id))) {
    const members = args.nodes.filter((n) => isLeaf(n) && n.parentId === team.id)
    teams.set(team.id, sizeTeam(team, members, args.edges, args.params))
    for (const member of members) teamOf.set(member.id, team.id)
  }
  return { teams, teamOf }
}

/** A department's own members, in emission order: team boxes and loose leaves. */
function departmentMembers(
  args: HierarchyArgs,
  departmentId: string,
  context: {
    departmentOf: ReadonlyMap<string, string>
    teamIds: ReadonlySet<string>
    teams: ReadonlyMap<string, SizedUnit>
  },
): Node[] {
  const members: Node[] = []
  for (const node of args.nodes) {
    if (node.type === 'department') continue
    if (context.departmentOf.get(node.id) !== departmentId) continue
    if (context.teamIds.has(node.id)) {
      const team = context.teams.get(node.id)
      if (team) members.push(team.node)
      continue
    }
    // A leaf inside a team is already drawn inside that team's box.
    if (node.parentId !== undefined && context.teamIds.has(node.parentId)) continue
    members.push(node)
  }
  return members
}

/** The edges with both endpoints among the given members. */
function scopeEdgesTo(edges: readonly Edge[], members: readonly Node[]): Edge[] {
  const memberIds = new Set(members.map((m) => m.id))
  return edges.filter((e) => memberIds.has(e.source) && memberIds.has(e.target))
}

/**
 * What the top-level frame arranges: every department as one box, staffed or
 * not, plus the nodes that belong to no department, in emission order.
 *
 * A leaf already drawn inside a team box is excluded even when no department
 * claims it. A `parentId` cycle among teams leaves their members with no
 * resolvable department, and without this they would be placed twice: once
 * here on the canvas and once inside the team box, which React Flow reads as
 * one id at two positions. The exclusion is for leaves only, since a team
 * parented to another team is not one of that team's members and so is drawn
 * nowhere unless this frame places it.
 */
function collectTopLevel(
  nodes: readonly Node[],
  boxes: {
    departments: ReadonlyMap<string, SizedUnit>
    departmentOf: ReadonlyMap<string, string>
    teamIds: ReadonlySet<string>
  },
): Node[] {
  const topLevel: Node[] = []
  for (const node of nodes) {
    if (node.type === 'department') {
      const box = boxes.departments.get(node.id)?.node
      if (box) topLevel.push(box)
      continue
    }
    if (boxes.departmentOf.has(node.id)) continue
    if (isTeamMember(node, boxes.teamIds)) continue
    topLevel.push(node)
  }
  return topLevel
}

/**
 * Size every unit from the inside out: teams first, then the departments that
 * contain them, leaving the top-level frame a flat set of boxes to arrange.
 */
export function planHierarchy(args: HierarchyArgs): HierarchyPlan {
  const departmentNodes = args.nodes.filter((n) => n.type === 'department')
  const departmentIds = new Set(departmentNodes.map((n) => n.id))
  const teamIds = new Set(args.nodes.filter((n) => n.type === 'team').map((n) => n.id))
  const departmentOf = resolveDepartmentOf(
    args.nodes,
    departmentIds,
    args.nodes.filter((n) => !departmentIds.has(n.id)),
  )

  const { teams, teamOf } = planTeams(args, teamIds)
  const teamScopedEdges = liftEdges(args.edges, teamOf)

  // An unstaffed department is sized and placed by the same frame as the rest,
  // so it keeps the slot the operator gave it rather than being appended to
  // the end of the row.
  const departments = new Map<string, SizedUnit>()
  for (const department of departmentNodes) {
    const members = departmentMembers(args, department.id, { departmentOf, teamIds, teams })
    departments.set(
      department.id,
      sizeDepartment(
        department,
        members,
        scopeEdgesTo(teamScopedEdges, members),
        args.params,
        args.chrome,
      ),
    )
  }

  return {
    topLevelNodes: collectTopLevel(args.nodes, { departments, departmentOf, teamIds }),
    topLevelEdges: liftEdges(args.edges, departmentOf),
    departments,
    teams,
  }
}
