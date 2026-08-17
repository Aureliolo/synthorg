import type { Edge, Node } from '@xyflow/react'
import { planRanks } from './layout-clusters'
import { flowIntoGrid, gridColumnCount } from './layout-grid'
import { runDagreLayout, type DagreParams } from './layout-graph'
import {
  EMPTY_GROUP_HEIGHT,
  EMPTY_GROUP_MIN_WIDTH,
  EMPTY_TEAM_HEIGHT,
  EMPTY_TEAM_WIDTH,
  POPULATED_GROUP_MIN_WIDTH,
  TEAM_HEADER_HEIGHT,
  TEAM_PADDING,
  WRAPPED_RANK_GAP_Y,
  getNodeDim,
} from './layout-shared'

/** A unit's members laid out on their own, measured from their own top-left. */
interface LaidOutUnit {
  readonly contentWidth: number
  readonly contentHeight: number
  /** Members positioned relative to the content's top-left corner. */
  readonly membersRelativeToContent: Node[]
}

/** A group box sized around its contents, plus those contents. */
export interface SizedUnit {
  /** The group node, sized; its position is assigned by the enclosing frame. */
  readonly node: Node
  /** Members positioned relative to the group box's top-left corner. */
  readonly childrenRelative: Node[]
}

/** Set an explicit rendered size on a group node. */
function sized(node: Node, width: number, height: number): Node {
  return { ...node, width, height, style: { ...node.style, width, height } }
}

/** Horizontal midpoint of a set of nodes. */
function centreX(nodes: readonly Node[]): number {
  let low = Infinity
  let high = -Infinity
  for (const node of nodes) {
    low = Math.min(low, node.position.x)
    high = Math.max(high, node.position.x + getNodeDim(node).w)
  }
  return (low + high) / 2
}

/**
 * Centre a unit's lead across the members that report to it.
 *
 * dagre balances a parent between its children rather than centring it exactly,
 * so the lead sits slightly off the midpoint and the head-to-report connectors
 * fan instead of forming a clean T-junction.
 *
 * Only the lead's own reports count. A member with no edge to the lead (a
 * department admin, say) shares the lead's rank, and centring the lead across
 * it would slide the lead into it.
 */
function centerLeadAcrossReports(members: Node[], edges: readonly Edge[]): Node[] {
  const lead = members.find((m) => m.data['isDeptLead'] === true)
  if (!lead) return members
  const reportIds = new Set(edges.filter((e) => e.source === lead.id).map((e) => e.target))
  const reports = members.filter((m) => reportIds.has(m.id))
  if (reports.length === 0) return members
  const midpoint = centreX(reports)
  const centred: Node = {
    ...lead,
    position: { x: midpoint - getNodeDim(lead).w / 2, y: lead.position.y },
  }
  return members.map((m) => (m.id === lead.id ? centred : m))
}

/** One rank of a unit, its members in dagre's left-to-right order. */
interface RankGroup {
  readonly centreY: number
  readonly members: Node[]
}

/**
 * Split a laid-out unit into its ranks.
 *
 * dagre gives every node on a rank the same rank centre, so the centre is the
 * rank's identity. The top edge is not: a taller card on the same rank starts
 * higher than its neighbours.
 */
function groupIntoRanks(positioned: readonly Node[]): RankGroup[] {
  const byRank = new Map<number, Node[]>()
  for (const node of positioned) {
    const key = Math.round(node.position.y + getNodeDim(node).h / 2)
    const bucket = byRank.get(key) ?? []
    bucket.push(node)
    byRank.set(key, bucket)
  }
  return [...byRank.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([centreY, members]) => ({
      centreY,
      members: [...members].sort((a, b) => a.position.x - b.position.x),
    }))
}

/** Move a rank's members so the topmost sits at `top`, keeping their spread. */
function restackInPlace(members: readonly Node[], top: number): Node[] {
  const minY = Math.min(...members.map((m) => m.position.y))
  return members.map((m) => ({
    ...m,
    position: { x: m.position.x, y: top + (m.position.y - minY) },
  }))
}

/** Vertical extent a rank occupies, tallest member included. */
function rankHeight(members: readonly Node[]): number {
  const minY = Math.min(...members.map((m) => m.position.y))
  const maxY = Math.max(...members.map((m) => m.position.y + getNodeDim(m).h))
  return maxY - minY
}

/**
 * Place one rank, wrapping it into a block when it is too wide to be a line.
 *
 * A rank that fits keeps the x dagre gave it, because those positions carry
 * something a grid cannot reproduce: each parent's children sit under that
 * parent. A rank that wraps has already given that up by definition, so it is
 * re-flowed as a block and centred where the line used to be, which leaves the
 * ranks above and below pointing at the same place.
 */
function placeRank(
  rank: RankGroup,
  top: number,
  params: DagreParams,
): { nodes: Node[]; height: number } {
  const boxes = rank.members.map((member) => {
    const { w, h } = getNodeDim(member)
    return { id: member.id, w, h }
  })
  if (gridColumnCount(boxes.length) >= boxes.length) {
    return { nodes: restackInPlace(rank.members, top), height: rankHeight(rank.members) }
  }
  const grid = flowIntoGrid(boxes, { gapX: params.nodeSep, gapY: WRAPPED_RANK_GAP_Y })
  const left = centreX(rank.members) - grid.width / 2
  const byId = new Map(rank.members.map((member) => [member.id, member]))
  const nodes = grid.placements.map((placement) => ({
    ...byId.get(placement.id)!,
    position: { x: left + placement.x, y: top + placement.y },
  }))
  return { nodes, height: grid.height }
}

/**
 * Wrap every over-wide rank and re-stack the unit from the top.
 *
 * Re-stacking is not optional: a rank that wrapped is taller than the line it
 * replaced, so every rank below it has to move down by the difference or the
 * block would grow straight through them.
 */
function wrapWideRanks(positioned: readonly Node[], params: DagreParams): Node[] {
  const out: Node[] = []
  let top = 0
  for (const rank of groupIntoRanks(positioned)) {
    const placed = placeRank(rank, top, params)
    out.push(...placed.nodes)
    top += placed.height + params.rankSep
  }
  return out
}

/**
 * Lay a unit's members out on their own.
 *
 * Each unit gets its own dagre graph rather than a compound cluster, so its
 * separations are the ones asked for here, and a unit that itself contains units
 * nests without limit.
 */
function layoutUnit(
  members: readonly Node[],
  edges: readonly Edge[],
  params: DagreParams,
): LaidOutUnit {
  const ranks = planRanks(members, edges)
  const positioned = [...runDagreLayout(members, edges, params, ranks.constraints).values()]
  const centred = centerLeadAcrossReports(wrapWideRanks(positioned, params), edges)

  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const member of centred) {
    const { w, h } = getNodeDim(member)
    minX = Math.min(minX, member.position.x)
    minY = Math.min(minY, member.position.y)
    maxX = Math.max(maxX, member.position.x + w)
    maxY = Math.max(maxY, member.position.y + h)
  }
  return {
    contentWidth: maxX - minX,
    contentHeight: maxY - minY,
    membersRelativeToContent: centred.map((member) => ({
      ...member,
      position: { x: member.position.x - minX, y: member.position.y - minY },
    })),
  }
}

/** Shift a unit's members into a box whose content starts at the given inset. */
function insetInto(unit: LaidOutUnit, insetX: number, insetY: number): Node[] {
  return unit.membersRelativeToContent.map((member) => ({
    ...member,
    position: { x: member.position.x + insetX, y: member.position.y + insetY },
  }))
}

/** Wrap a team's members in the team card's padding and title row. */
export function sizeTeam(
  team: Node,
  members: readonly Node[],
  edges: readonly Edge[],
  params: DagreParams,
): SizedUnit {
  if (members.length === 0) {
    return { node: sized(team, EMPTY_TEAM_WIDTH, EMPTY_TEAM_HEIGHT), childrenRelative: [] }
  }
  const unit = layoutUnit(members, edges, params)
  return {
    node: sized(
      team,
      unit.contentWidth + TEAM_PADDING * 2,
      unit.contentHeight + TEAM_PADDING * 2 + TEAM_HEADER_HEIGHT,
    ),
    childrenRelative: insetInto(unit, TEAM_PADDING, TEAM_PADDING + TEAM_HEADER_HEIGHT),
  }
}

export interface DepartmentChrome {
  readonly cardPadding: number
  readonly headerHeight: number
  readonly footerHeight: number
}

/**
 * Wrap a department's members in the card's padding, header and footer.
 *
 * The box handed back is the size the card actually renders at, so the frame
 * that places it separates departments by their true footprint rather than by
 * their contents alone. An unstaffed department is sized here too, from the
 * empty-state card's own bounds: `layoutUnit` seeds its bounding box with
 * infinities, so measuring nothing would hand React Flow a `NaN` geometry.
 */
export function sizeDepartment(
  department: Node,
  members: readonly Node[],
  edges: readonly Edge[],
  params: DagreParams,
  chrome: DepartmentChrome,
): SizedUnit {
  if (members.length === 0) {
    return {
      node: sized(department, EMPTY_GROUP_MIN_WIDTH, EMPTY_GROUP_HEIGHT),
      childrenRelative: [],
    }
  }
  const unit = layoutUnit(members, edges, params)
  const { cardPadding, headerHeight, footerHeight } = chrome
  const width = Math.max(unit.contentWidth + cardPadding * 2, POPULATED_GROUP_MIN_WIDTH)
  // A card held open to its minimum width centres its contents rather than
  // leaving the slack on one side.
  const leftInset = (width - unit.contentWidth) / 2
  return {
    node: sized(
      department,
      width,
      unit.contentHeight + cardPadding * 2 + headerHeight + footerHeight,
    ),
    childrenRelative: insetInto(unit, leftInset, cardPadding + headerHeight),
  }
}
