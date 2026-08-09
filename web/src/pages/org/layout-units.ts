import type { Edge, Node } from '@xyflow/react'
import { chooseClusterDirection, widestRank } from './layout-clusters'
import { runDagreLayout, type DagreParams } from './layout-graph'
import {
  type ClusterDirection,
  EMPTY_TEAM_HEIGHT,
  EMPTY_TEAM_WIDTH,
  POPULATED_GROUP_MIN_WIDTH,
  TEAM_HEADER_HEIGHT,
  TEAM_PADDING,
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

/** Midpoint of a set of nodes along the axis across the unit's flow. */
function crossAxisMidpoint(nodes: readonly Node[], direction: ClusterDirection): number {
  let low = Infinity
  let high = -Infinity
  for (const node of nodes) {
    const { w, h } = getNodeDim(node)
    const start = direction === 'LR' ? node.position.y : node.position.x
    const extent = direction === 'LR' ? h : w
    low = Math.min(low, start)
    high = Math.max(high, start + extent)
  }
  return (low + high) / 2
}

/**
 * Centre a unit's lead across the members that report to it.
 *
 * dagre balances a parent between its children rather than centring it
 * exactly, so the lead sits slightly off the midpoint and the head-to-report
 * connectors fan instead of forming a clean T-junction. The centring is across
 * the flow: on x for a top-to-bottom unit, on y for a left-to-right one, where
 * the lead sits beside its reports rather than above them.
 *
 * Only the lead's own reports count. A member with no edge to the lead (a
 * department admin, say) shares the lead's rank, and centring the lead across
 * it would slide the lead into it.
 */
function centerLeadAcrossReports(
  members: Node[],
  edges: readonly Edge[],
  direction: ClusterDirection,
): Node[] {
  const lead = members.find((m) => (m.data as { isDeptLead?: boolean }).isDeptLead === true)
  if (!lead) return members
  const reportIds = new Set(
    edges.filter((e) => e.source === lead.id).map((e) => e.target),
  )
  const reports = members.filter((m) => reportIds.has(m.id))
  if (reports.length === 0) return members
  const midpoint = crossAxisMidpoint(reports, direction)
  const { w, h } = getNodeDim(lead)
  const centred: Node = {
    ...lead,
    position:
      direction === 'LR'
        ? { x: lead.position.x, y: midpoint - h / 2 }
        : { x: midpoint - w / 2, y: lead.position.y },
  }
  return members.map((m) => (m.id === lead.id ? centred : m))
}

/**
 * Lay a unit's members out on their own, in the unit's own direction.
 *
 * Each unit gets its own dagre graph rather than a compound cluster, so its
 * direction AND its separations are the ones asked for here, and a unit that
 * itself contains units nests without limit.
 */
function layoutUnit(
  members: readonly Node[],
  edges: readonly Edge[],
  params: DagreParams,
): LaidOutUnit {
  const memberIds = members.map((m) => m.id)
  const direction = chooseClusterDirection(widestRank(memberIds, edges), params.nodeSep)
  const positioned = [...runDagreLayout(members, edges, { ...params, direction }).values()]
  const centred = centerLeadAcrossReports(positioned, edges, direction)

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
 * their contents alone.
 */
export function sizeDepartment(
  department: Node,
  members: readonly Node[],
  edges: readonly Edge[],
  params: DagreParams,
  chrome: DepartmentChrome,
): SizedUnit {
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
