import type { Edge, Node } from '@xyflow/react'
import { liftEdges, resolveDepartmentOf } from './layout-clusters'
import type { DagreParams } from './layout-graph'
import {
  type DepartmentChrome,
  type SizedUnit,
  sizeDepartment,
  sizeTeam,
  sized,
} from './layout-units'
import { EMPTY_GROUP_HEIGHT, EMPTY_GROUP_MIN_WIDTH } from './layout-shared'

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
  /** Empty-state department boxes, keyed by department id. */
  readonly emptyDepartments: Map<string, Node>
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
 */
function collectTopLevel(
  nodes: readonly Node[],
  boxes: {
    departments: ReadonlyMap<string, SizedUnit>
    emptyDepartments: ReadonlyMap<string, Node>
    departmentOf: ReadonlyMap<string, string>
  },
): Node[] {
  const topLevel: Node[] = []
  for (const node of nodes) {
    if (node.type !== 'department') {
      if (!boxes.departmentOf.has(node.id)) topLevel.push(node)
      continue
    }
    const box = boxes.departments.get(node.id)?.node ?? boxes.emptyDepartments.get(node.id)
    if (box) topLevel.push(box)
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

  const departments = new Map<string, SizedUnit>()
  const emptyDepartments = new Map<string, Node>()
  for (const department of departmentNodes) {
    const members = departmentMembers(args, department.id, { departmentOf, teamIds, teams })
    if (members.length === 0) {
      // An unstaffed department is still a box in the row, placed by the same
      // frame as the rest so it keeps the slot the operator gave it.
      emptyDepartments.set(
        department.id,
        sized(department, EMPTY_GROUP_MIN_WIDTH, EMPTY_GROUP_HEIGHT),
      )
      continue
    }
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
    topLevelNodes: collectTopLevel(args.nodes, { departments, emptyDepartments, departmentOf }),
    topLevelEdges: liftEdges(args.edges, departmentOf),
    departments,
    teams,
    emptyDepartments,
  }
}
