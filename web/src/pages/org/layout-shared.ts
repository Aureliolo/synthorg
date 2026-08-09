import type { Node } from '@xyflow/react'
import type { Density } from '@/stores/theme'

// The direction of the chart AS A WHOLE.  Only 'TB' is used: the
// post-layout adjustment pass (Steps 4-5) places owner row, root box and
// department row along the y-axis, and 'LR' would need all of it mirrored.
export type LayoutDirection = 'TB'

// The direction INSIDE one department card.  A department is laid out in
// isolation and placed as a single box by the passes above, so its own flow
// is contained by its card and leaves the global top-to-bottom reading intact.
export type ClusterDirection = 'TB' | 'LR'

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
  /**
   * The density the cards will actually render at. Department cards use the
   * density-aware `p-card` token, so the space this layout reserves for card
   * chrome only matches what appears on screen if it tracks the same axis.
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

// Matches the fixed `w-44` (176px) agent-card width in AgentNode so the
// layout estimate equals the rendered width and sibling edge centres align.
export const DEFAULT_NODE_WIDTH = 176
export const DEFAULT_NODE_HEIGHT = 80

// Separation between two cards on the same rank, and between two ranks. Every
// unit is laid out in its own graph, so these are the values that actually
// apply inside every card as well as between the department boxes.
export const DEFAULT_NODE_SEP = 60
export const DEFAULT_RANK_SEP = 50
// A department card's inner padding comes from the density-aware `p-card`
// token, so the reserve has to track the same axis; these mirror
// `--so-density-card-padding` in styles/design-tokens.css.
const CARD_PADDING_BY_DENSITY: Record<Density, number> = {
  dense: 12,
  medium: 14,
  balanced: 16,
  sparse: 20,
}
const DEFAULT_GROUP_PADDING = CARD_PADDING_BY_DENSITY.balanced

/** Inner padding a department card renders at the given density. */
export function cardPaddingFor(density: Density | undefined): number {
  return density === undefined ? DEFAULT_GROUP_PADDING : CARD_PADDING_BY_DENSITY[density]
}

// Title row plus the bottom margin under it, which leaves a small breathing
// gap between the stats (Active / Cost) row and the first agent card below.
const HEADER_TITLE_ROW = 40
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

// Widest a department card may get before it flows left-to-right instead.
// Every department sits in the same row under the root, so their widths add
// up while their heights only take the max: past three minimum-width cards a
// single flat department dominates the canvas and shoves its siblings out of
// view, where the same members in a column cost nothing but row height.
export const DEPT_HORIZONTAL_WIDTH_BUDGET = POPULATED_GROUP_MIN_WIDTH * 3

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

/**
 * Height of the department card's header content.
 *
 * The card's own padding is not part of it: the sizing pass reserves one band
 * on every side, so counting the top one here would reserve it twice and leave
 * a blank strip between the stats row and the first agent card.
 */
export function computeHeaderHeight(prefs: LayoutVisualPrefs): number {
  let h = HEADER_TITLE_ROW + HEADER_STATS_BAR
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
