import type { Node } from '@xyflow/react'
import type { Density } from '@/stores/theme'
import { AGENT_CARD_WIDTH, agentCardSize, cardPaddingFor } from './card-metrics'

// The chart as a whole flows top to bottom. Siblings that would overrun the
// canvas wrap into a block rather than turning the flow on its side, so there
// is no second direction for any frame to be laid out in.
export type LayoutDirection = 'TB'

/**
 * Per-render visual preferences that affect how much space the dept
 * card chrome takes up.  Passed through from `useOrgChartData` so the
 * layout reserves exactly the space that will actually be rendered.
 */
export interface LayoutVisualPrefs {
  showBudgetBar?: boolean
  showStatusDots?: boolean
  showAddAgentButton?: boolean
}

export interface LayoutOptions extends LayoutVisualPrefs {
  direction?: LayoutDirection
  nodeSep?: number
  rankSep?: number
  /**
   * The density the cards will actually render at. Both card types use the
   * density-aware `p-card` token, so the space this layout reserves only
   * matches what appears on screen if it tracks the same axis.
   */
  density?: Density
}

/**
 * A department group node with its post-layout size and the nodes drawn
 * inside it, whose positions are relative to the box rather than the canvas.
 */
export interface GroupResult {
  node: Node
  childrenRelative: Node[]
  groupWidth: number
  groupHeight: number
}

export { cardPaddingFor }

// Fallback footprint for a node the chart does not size itself. Agent cards are
// sized from `card-metrics`, so this only backs a node type with no declared
// geometry at all.
export const DEFAULT_NODE_WIDTH = AGENT_CARD_WIDTH
export const DEFAULT_NODE_HEIGHT = 66

// Separation between two cards on the same rank, and between two ranks. Every
// unit is laid out in its own graph, so these are the values that actually
// apply inside every card as well as between the department boxes.
export const DEFAULT_NODE_SEP = 60
export const DEFAULT_RANK_SEP = 50

// Vertical gap between the rows of a wrapped rank. A rank's members are
// siblings, so no edge runs between the rows and they need no rank separation;
// they need only enough room for a connector's bus to pass between them.
export const WRAPPED_RANK_GAP_Y = 20

// Team card chrome: `p-2` padding on all sides plus a `text-xs` title row
// with a `pb-1` gap under it, matching what TeamGroupNode renders.
export const TEAM_PADDING = 8
export const TEAM_HEADER_HEIGHT = 20

// A team can exist before it is staffed, so it has no members to derive
// bounds from and is laid out as a plain card of its own.
export const EMPTY_TEAM_WIDTH = 200
export const EMPTY_TEAM_HEIGHT = 64

export const EMPTY_GROUP_MIN_WIDTH = 240
// Matches the empty-state card's min-h -- header + "No agents yet"
// icon + label + (optional) add agent chip.
export const EMPTY_GROUP_HEIGHT = 180

// Minimum width for a POPULATED dept card. Wide enough that the
// Active / Cost stat pills sit on a single row instead of wrapping and
// clipping past the card's left edge, and so a 1-agent dept reads with the
// same visual weight as a multi-agent one.
export const POPULATED_GROUP_MIN_WIDTH = 340

// Target visible gap between the chart's stacked bands (owner row, root box,
// department block). Enforced AFTER dagre by a manual shift pass, not by
// dagre's minlen, whose integer ranks quantize into rankSep-sized jumps and so
// changed the gap whenever the header chrome crossed a boundary.
export const DESIRED_INTER_DEPT_GAP = 48

// Horizontal gap between adjacent department boxes in the department block.
export const DESIRED_INTER_DEPT_GAP_X = 56

/**
 * The footprint a node occupies, preferring what it declares over a fallback.
 *
 * A measured size is preferred where React Flow has one, but an agent card is
 * sized from `card-metrics` before the first paint precisely so the reserve does
 * not depend on a measurement that only exists after it.
 */
export function getNodeDim(node: Node): { w: number; h: number } {
  const w = node.measured?.width ?? node.width ?? DEFAULT_NODE_WIDTH
  const h = node.measured?.height ?? node.height ?? DEFAULT_NODE_HEIGHT
  return { w, h }
}

/**
 * Give every agent card the footprint it will render at.
 *
 * Without this the layout falls back to a default height, and a default that
 * disagrees with the card leaves either dead space inside the department box or
 * a card overlapping the chrome below it. The size travels on the node, so the
 * same number reaches dagre, the box that wraps it and the rendered element.
 */
export function sizeAgentNodes(nodes: readonly Node[], density: Density | undefined): Node[] {
  const { width, height } = agentCardSize(density)
  return nodes.map((node) =>
    node.type === 'agent'
      ? { ...node, width, height, style: { ...node.style, width, height } }
      : node,
  )
}
