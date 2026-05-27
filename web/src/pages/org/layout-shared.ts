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

export const DEFAULT_NODE_WIDTH = 160
export const DEFAULT_NODE_HEIGHT = 80
export const DEFAULT_GROUP_PADDING = 16

// Fixed header pieces on every dept card (inner padding + title row + bottom margin)
const HEADER_BASE = 48
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

// Target visible gap between any two adjacent dept boxes.  Enforced
// AFTER dagre by a manual shift pass, not by dagre's minlen (dagre's
// integer ranks quantize into 50 px jumps depending on how close the
// header chrome is to a rankSep boundary, which made the gap change
// when the user toggled status dots on/off).
export const DESIRED_INTER_DEPT_GAP = 48

// Static minlens used only to keep dagre's ranking correct (so it
// doesn't compact the graph into a single rank).  Actual spacing
// comes from the post-shift pass.
export const OWNER_TO_ROOT_MINLEN = 2
export const CEO_TO_CHILD_MINLEN = 2

export function computeHeaderHeight(prefs: LayoutVisualPrefs): number {
  let h = HEADER_BASE
  if (prefs.showBudgetBar) h += HEADER_BUDGET_BAR
  if (prefs.showStatusDots) h += HEADER_STATUS_DOTS
  return h
}

export function computeFooterHeight(prefs: LayoutVisualPrefs): number {
  return prefs.showAddAgentButton ? FOOTER_ADD_AGENT : 0
}

export function getNodeDim(node: Node): { w: number; h: number } {
  const w = node.measured?.width ?? (node.width as number | undefined) ?? DEFAULT_NODE_WIDTH
  const h = node.measured?.height ?? (node.height as number | undefined) ?? DEFAULT_NODE_HEIGHT
  return { w, h }
}
