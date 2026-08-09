import type { Edge, Node } from '@xyflow/react'
import { deriveLayoutModel } from './layout-clusters'
import { runDagreLayout, type DagreParams } from './layout-graph'
import {
  EMPTY_TEAM_HEIGHT,
  EMPTY_TEAM_WIDTH,
  TEAM_HEADER_HEIGHT,
  TEAM_PADDING,
  getNodeDim,
} from './layout-shared'

/** What the main graph and the caller need once teams are folded up. */
export interface TeamPlan {
  /** Nodes the main dagre pass positions: loose leaves plus sized team boxes. */
  readonly leafNodes: Node[]
  /** Edges with every team member folded into the team box that holds it. */
  readonly edges: Edge[]
  /** Team members, already positioned relative to their team box. */
  readonly memberNodes: Node[]
}

/** A team box plus its members, already relative to that box. */
interface LaidOutTeam {
  readonly node: Node
  readonly memberNodes: Node[]
}

/** Set an explicit rendered size on a group node. */
function sized(node: Node, width: number, height: number): Node {
  return { ...node, width, height, style: { ...node.style, width, height } }
}

/** Nodes dagre positions directly: neither a department nor a team box. */
function isLeaf(node: Node): boolean {
  return node.type !== 'department' && node.type !== 'team'
}

/** Lay a team's members out on their own and size the box around them. */
function layoutOneTeam(
  team: Node,
  members: Node[],
  edges: readonly Edge[],
  params: DagreParams,
): LaidOutTeam {
  const memberIds = new Set(members.map((m) => m.id))
  const internalEdges = edges.filter(
    (e) => memberIds.has(e.source) && memberIds.has(e.target),
  )
  const model = deriveLayoutModel({
    groupNodes: [],
    allNodes: members,
    leafNodes: members,
    edges: internalEdges,
    nodeSep: params.nodeSep,
  })
  const positioned = [...runDagreLayout(members, internalEdges, model, params).values()]

  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const member of positioned) {
    const { w, h } = getNodeDim(member)
    minX = Math.min(minX, member.position.x)
    minY = Math.min(minY, member.position.y)
    maxX = Math.max(maxX, member.position.x + w)
    maxY = Math.max(maxY, member.position.y + h)
  }
  const originX = minX - TEAM_PADDING
  const originY = minY - TEAM_PADDING - TEAM_HEADER_HEIGHT

  return {
    node: sized(
      team,
      maxX - minX + TEAM_PADDING * 2,
      maxY - minY + TEAM_PADDING * 2 + TEAM_HEADER_HEIGHT,
    ),
    memberNodes: positioned.map((member) => ({
      ...member,
      position: { x: member.position.x - originX, y: member.position.y - originY },
    })),
  }
}

/** Lay out every team box in the tree, keyed by the team node's id. */
function layoutEveryTeam(
  nodes: readonly Node[],
  edges: readonly Edge[],
  params: DagreParams,
): Map<string, LaidOutTeam> {
  const teams = new Map<string, LaidOutTeam>()
  for (const team of nodes.filter((n) => n.type === 'team')) {
    const members = nodes.filter((n) => isLeaf(n) && n.parentId === team.id)
    teams.set(
      team.id,
      members.length === 0
        ? { node: sized(team, EMPTY_TEAM_WIDTH, EMPTY_TEAM_HEIGHT), memberNodes: [] }
        : layoutOneTeam(team, members, edges, params),
    )
  }
  return teams
}

/** Rewrite an endpoint that sits inside a team to the team box itself. */
function foldEdges(edges: readonly Edge[], teamOf: ReadonlyMap<string, string>): Edge[] {
  const seen = new Set<string>()
  const folded: Edge[] = []
  for (const edge of edges) {
    const source = teamOf.get(edge.source) ?? edge.source
    const target = teamOf.get(edge.target) ?? edge.target
    if (source === target) continue
    const key = `${source} ${target}`
    if (seen.has(key)) continue
    seen.add(key)
    folded.push(source === edge.source && target === edge.target
      ? edge
      : { ...edge, id: `fold:${key}`, source, target })
  }
  return folded
}

/**
 * Fold every team into a single sized box before the main layout runs.
 *
 * dagre 3.1.0 cannot nest a directed cluster inside another: it copies only a
 * cluster's direct children into the isolated subgraph, so a team's members
 * are stripped from the parent graph and never repositioned. Laying each team
 * out on its own and handing the main pass one node of the right size gets the
 * same result without that path, and it means the space the team card occupies
 * -- chrome included -- is reserved by dagre rather than patched up afterwards.
 */
export function planTeams(
  nodes: readonly Node[],
  edges: readonly Edge[],
  params: DagreParams,
): TeamPlan {
  const teams = layoutEveryTeam(nodes, edges, params)
  if (teams.size === 0) {
    return { leafNodes: nodes.filter(isLeaf), edges: [...edges], memberNodes: [] }
  }

  const teamOf = new Map<string, string>()
  const memberNodes: Node[] = []
  // Walk `nodes` in emission order so the ordering constraints derived from
  // the leaf list keep reading the operator's arrangement.
  const leafNodes: Node[] = []
  for (const node of nodes) {
    const team = teams.get(node.id)
    if (team) {
      leafNodes.push(team.node)
      memberNodes.push(...team.memberNodes)
      for (const member of team.memberNodes) teamOf.set(member.id, node.id)
      continue
    }
    const foldedIntoTeam = node.parentId !== undefined && teams.has(node.parentId)
    if (isLeaf(node) && !foldedIntoTeam) leafNodes.push(node)
  }

  return { leafNodes, edges: foldEdges(edges, teamOf), memberNodes }
}
