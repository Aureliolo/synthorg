import type { Edge, Node } from '@xyflow/react'
import { liftEdges, resolveDepartmentOf } from './layout-clusters'
import type { DagreParams } from './layout-graph'
import { type DepartmentChrome, type SizedUnit, sizeDepartment, sizeTeam } from './layout-units'
import {
  type GroupResult,
  DEFAULT_GROUP_PADDING,
  EMPTY_GROUP_HEIGHT,
  EMPTY_GROUP_MIN_WIDTH,
} from './layout-shared'

/** The dept group ids flagged as the root by build-org-tree. */
export function collectRootGroupIds(groupNodes: readonly Node[]): Set<string> {
  const rootGroupIds = new Set<string>()
  for (const group of groupNodes) {
    if ((group.data as { isRootDepartment?: boolean }).isRootDepartment) {
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
  /** Departments with no members to wrap. */
  readonly emptyDepartments: Node[]
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
  const emptyDepartments: Node[] = []
  for (const department of departmentNodes) {
    const members = departmentMembers(args, department.id, { departmentOf, teamIds, teams })
    if (members.length === 0) {
      emptyDepartments.push(department)
      continue
    }
    const memberIds = new Set(members.map((m) => m.id))
    const internalEdges = teamScopedEdges.filter(
      (e) => memberIds.has(e.source) && memberIds.has(e.target),
    )
    departments.set(
      department.id,
      sizeDepartment(department, members, internalEdges, args.params, args.chrome),
    )
  }

  const liftToTop = new Map(departmentOf)
  const topLevelNodes: Node[] = []
  for (const node of args.nodes) {
    if (node.type === 'department') {
      const department = departments.get(node.id)
      if (department) topLevelNodes.push(department.node)
      continue
    }
    if (!departmentOf.has(node.id)) topLevelNodes.push(node)
  }

  return {
    topLevelNodes,
    topLevelEdges: liftEdges(args.edges, liftToTop),
    departments,
    teams,
    emptyDepartments,
  }
}

/**
 * Place departments with no members into the non-root row, before the centring
 * pass, so they are part of the cluster that gets centred rather than appended
 * asymmetrically afterwards.
 */
export function placeEmptyGroups(
  emptyGroups: readonly Node[],
  populatedResults: readonly GroupResult[],
  rootGroupIds: ReadonlySet<string>,
  rootPopulated: GroupResult | undefined,
): GroupResult[] {
  const emptyResults: GroupResult[] = []
  const populatedNonRoot = populatedResults.filter((r) => !rootGroupIds.has(r.node.id))

  let nonRootRowY = 0
  let nonRootRowRightEdge = 0
  if (populatedNonRoot.length > 0) {
    nonRootRowY = Math.min(...populatedNonRoot.map((r) => r.node.position.y))
    nonRootRowRightEdge = Math.max(
      ...populatedNonRoot.map((r) => r.node.position.x + r.groupWidth),
    )
  } else if (rootPopulated) {
    // No populated non-root depts: fall back to placing empty depts below the
    // root dept (edge case: an org with only a CEO).
    nonRootRowY =
      rootPopulated.node.position.y + rootPopulated.groupHeight + DEFAULT_GROUP_PADDING
    nonRootRowRightEdge = rootPopulated.node.position.x + rootPopulated.groupWidth
  }

  for (const group of emptyGroups) {
    const isRoot = rootGroupIds.has(group.id)
    let groupX: number
    let groupY: number
    if (isRoot) {
      // An empty ROOT dept (no CEO, very unusual) is anchored above the row.
      groupX = nonRootRowRightEdge - EMPTY_GROUP_MIN_WIDTH
      groupY = nonRootRowY - EMPTY_GROUP_HEIGHT - DEFAULT_GROUP_PADDING * 2
    } else {
      groupX = nonRootRowRightEdge + DEFAULT_GROUP_PADDING
      groupY = nonRootRowY
      nonRootRowRightEdge = groupX + EMPTY_GROUP_MIN_WIDTH
    }
    emptyResults.push({
      node: {
        ...group,
        position: { x: groupX, y: groupY },
        width: EMPTY_GROUP_MIN_WIDTH,
        height: EMPTY_GROUP_HEIGHT,
        style: { ...group.style, width: EMPTY_GROUP_MIN_WIDTH, height: EMPTY_GROUP_HEIGHT },
      },
      childrenRelative: [],
      groupWidth: EMPTY_GROUP_MIN_WIDTH,
      groupHeight: EMPTY_GROUP_HEIGHT,
    })
  }
  return emptyResults
}
