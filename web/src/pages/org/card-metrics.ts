/**
 * The one place that says how tall a chart card's parts are.
 *
 * React Flow draws a department's agents as absolutely positioned children of
 * the department box, so the layout has to reserve the header band before the
 * header exists to measure. That reserve and the header CSS were two separate
 * descriptions of the same band, and they disagreed: the budget row reserved 26
 * px and rendered 36, so the stats pill row overlapped the first agent card by 6
 * px on every department, while the agent card reserved 80 px and rendered 66,
 * leaving 14 px of dead space in the opposite direction.
 *
 * So there is one owner. Every height here is applied to the rendered element by
 * `DepartmentGroupNode` AND summed by the layout, which makes the reserve exact
 * by construction rather than by a constant somebody remembered to update. A
 * restyle that changes a row's height has to change the row's height HERE, and
 * both sides move together.
 */

import type { Density } from '@/stores/theme'

/** Which rows a department card's header shows, in render order. */
export type DeptHeaderRowKind = 'title' | 'budget' | 'dots' | 'stats'

// Mirrors `--so-density-card-padding` in styles/design-tokens.css, which is what
// the `p-card` utility on both card types resolves to.
const CARD_PADDING_BY_DENSITY: Record<Density, number> = {
  dense: 12,
  medium: 14,
  balanced: 16,
  sparse: 20,
}
const DEFAULT_CARD_PADDING = CARD_PADDING_BY_DENSITY.balanced

/** Inner padding a card renders at the given density. */
export function cardPaddingFor(density: Density | undefined): number {
  return density === undefined ? DEFAULT_CARD_PADDING : CARD_PADDING_BY_DENSITY[density]
}

/** Vertical gap between header rows; mirrors the header block's `space-y-1.5`. */
export const DEPT_HEADER_ROW_GAP = 6

/**
 * Breathing room between the last header row and the first agent card.
 *
 * Part of the reserve rather than a margin on the header, because the agent
 * cards are not in the header's flow and a margin would not push them.
 */
export const DEPT_HEADER_TRAILING_GAP = 8

/** Height of each header row's own box, gaps excluded. */
export const DEPT_HEADER_ROW_HEIGHT: Record<DeptHeaderRowKind, number> = {
  // Department name, collapse chevron and the agent-count pill on one line.
  title: 30,
  // The `budget` / `active` label line plus the utilisation meter under it.
  budget: 30,
  // A row of `size-2.5` dots on a `pt-1` line. The `ring-2` on each dot is a
  // box-shadow, which paints outside the box and adds nothing to the height.
  dots: 14,
  // One row of stat pills (Active, Cost). POPULATED_GROUP_MIN_WIDTH keeps the
  // card wide enough that these never wrap to a second line.
  stats: 30,
}

/**
 * Height of the "+ Add agent" footer block.
 *
 * The chip itself is 30 px and sits on a `pt-5` line, and that padding is the
 * visible gap between the last agent card and the chip.
 */
const DEPT_FOOTER_ADD_AGENT_HEIGHT = 50

/**
 * An agent card's content height: the avatar beside the name and role lines.
 *
 * Its padding is added per density, because `AgentNode` carries the same
 * `p-card` token as the department box it sits in.
 */
const AGENT_CARD_CONTENT_HEIGHT = 42

// Matches the fixed `w-44` on AgentNode. A uniform width means a node's
// top-left alignment and its centre alignment agree, so sibling edges leave
// from a common line instead of picking up a jog.
export const AGENT_CARD_WIDTH = 176

/** The footprint an agent card renders at, which the layout must reserve. */
export function agentCardSize(density: Density | undefined): {
  width: number
  height: number
} {
  return {
    width: AGENT_CARD_WIDTH,
    height: cardPaddingFor(density) * 2 + AGENT_CARD_CONTENT_HEIGHT,
  }
}

/** Everything the header's row set depends on, from prefs and the department. */
export interface DeptHeaderInputs {
  readonly showBudgetBar: boolean
  readonly showStatusDots: boolean
  readonly showAddAgentButton: boolean
  readonly budgetPercent: number | null
  readonly statusDotCount: number
  readonly isEmpty: boolean
  readonly isCollapsed: boolean
}

/**
 * The header rows this department actually shows, in render order.
 *
 * Both consumers read this rather than each testing the conditions themselves.
 * A toggle is not enough on its own: the budget row also needs an allocation to
 * show, so reserving on the toggle alone left an unallocated department with a
 * blank 26 px strip inside its header.
 */
export function deptHeaderRows(inputs: DeptHeaderInputs): DeptHeaderRowKind[] {
  const rows: DeptHeaderRowKind[] = ['title']
  if (inputs.showBudgetBar && inputs.budgetPercent !== null && inputs.budgetPercent > 0) {
    rows.push('budget')
  }
  if (inputs.showStatusDots && inputs.statusDotCount > 0) {
    rows.push('dots')
  }
  if (!inputs.isEmpty && !inputs.isCollapsed) {
    rows.push('stats')
  }
  return rows
}

/** Height of the header rows plus the gaps between them, trailing gap excluded. */
export function deptHeaderContentHeight(inputs: DeptHeaderInputs): number {
  const rows = deptHeaderRows(inputs)
  const gaps = Math.max(0, rows.length - 1) * DEPT_HEADER_ROW_GAP
  return rows.reduce((total, row) => total + DEPT_HEADER_ROW_HEIGHT[row], gaps)
}

/** What the layout reserves above the first agent card. */
export function deptHeaderHeight(inputs: DeptHeaderInputs): number {
  return deptHeaderContentHeight(inputs) + DEPT_HEADER_TRAILING_GAP
}

/** What the layout reserves below the last agent card. */
export function deptFooterHeight(inputs: DeptHeaderInputs): number {
  return inputs.showAddAgentButton && !inputs.isEmpty ? DEPT_FOOTER_ADD_AGENT_HEIGHT : 0
}
