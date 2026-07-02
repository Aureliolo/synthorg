import type { Node } from '@xyflow/react'

// Only 'TB' is currently used.  The post-layout adjustment pass
// (Steps 4-5) assumes a top-to-bottom layout.  Adding 'LR' support
// would require mirroring those steps along the x-axis.
export type LayoutDirection = 'TB'

/**
 * Per-render visual preferences that affect how much space the dept
 * card chrome takes up.  Passed through from `useOrgChartData` so the
 * layout reserves exactly the space that will actually be rendered --
 * no more "100 px of empty space when the user turns toggles off" bug.
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
}

/** A department group node plus its post-layout bounds + children. */
export interface GroupResult {
  node: Node
  children: Node[]
  groupX: number
  groupY: number
  groupWidth: number
  groupHeight: number
}

// Matches the fixed `w-44` (176px) agent-card width in AgentNode so the
// layout estimate equals the rendered width and sibling edge centres align.
export const DEFAULT_NODE_WIDTH = 176
export const DEFAULT_NODE_HEIGHT = 80
export const DEFAULT_GROUP_PADDING = 16

// Fixed header pieces on every dept card (inner padding + title row + bottom
// margin). The bottom margin leaves a small breathing gap between the stats
// (Active / Cost) row and the first agent card below it.
const HEADER_BASE = 56
// Department stats pill row (Active / Cost) rendered on every populated,
// expanded dept card. One StatPill row is ~22 px plus the header's
// space-y-1.5 (6 px) gap above it. Not gated by a view toggle, so it is
// always reserved -- omitting it let child agent cards overlap it.
const HEADER_STATS_BAR = 30
// Added when budget bar is on (label + 1 px bar + spacing)
const HEADER_BUDGET_BAR = 26
// Added when status dots are on.  The dots are `size-2.5` (10 px) +
// `ring-2` (4 px per side) and sit on a `pt-1` (4 px) padding line, so
// the row occupies roughly 18 px of vertical space inside the header.
const HEADER_STATUS_DOTS = 20
// Bottom footer chip ("+ Add agent")
const FOOTER_ADD_AGENT = 34

export const EMPTY_GROUP_MIN_WIDTH = 240
// Matches the empty-state card's min-h -- header + "No agents yet"
// icon + label + (optional) add agent chip.
export const EMPTY_GROUP_HEIGHT = 180

// Minimum width for a POPULATED dept card. Wide enough that the
// Active / Cost stat pills sit on a single row instead of wrapping and
// clipping past the card's left edge, and so a 1-agent dept reads with the
// same visual weight as a multi-agent one.
export const POPULATED_GROUP_MIN_WIDTH = 340

// Target visible gap between any two adjacent dept boxes.  Enforced
// AFTER dagre by a manual shift pass, not by dagre's minlen (dagre's
// integer ranks quantize into 50 px jumps depending on how close the
// header chrome is to a rankSep boundary, which made the gap change
// when the user toggled status dots on/off).
export const DESIRED_INTER_DEPT_GAP = 48

// Horizontal gap enforced between adjacent sibling dept cards in the
// non-root row. Dagre only separates the leaf agents (by nodeSep), not
// the dept BOXES that wrap them, so wide multi-agent depts would otherwise
// overlap their neighbours. A dedicated de-overlap pass uses this.
export const DESIRED_INTER_DEPT_GAP_X = 56

// Static minlens used only to keep dagre's ranking correct (so it
// doesn't compact the graph into a single rank).  Actual spacing
// comes from the post-shift pass.
export const OWNER_TO_ROOT_MINLEN = 2
export const CEO_TO_CHILD_MINLEN = 2

export function computeHeaderHeight(prefs: LayoutVisualPrefs): number {
  let h = HEADER_BASE + HEADER_STATS_BAR
  if (prefs.showBudgetBar) h += HEADER_BUDGET_BAR
  if (prefs.showStatusDots) h += HEADER_STATUS_DOTS
  return h
}

export function computeFooterHeight(prefs: LayoutVisualPrefs): number {
  return prefs.showAddAgentButton ? FOOTER_ADD_AGENT : 0
}

export function getNodeDim(node: Node): { w: number; h: number } {
  const w = node.measured?.width ?? (node.width) ?? DEFAULT_NODE_WIDTH
  const h = node.measured?.height ?? (node.height) ?? DEFAULT_NODE_HEIGHT
  return { w, h }
}
