import { describe, expect, it } from 'vitest'
import type { Node } from '@xyflow/react'
import {
  type DeptHeaderInputs,
  cardPaddingFor,
  deptFooterHeight,
  deptHeaderHeight,
} from '@/pages/org/card-metrics'
import { gridColumnCount } from '@/pages/org/layout-grid'
import {
  DEFAULT_NODE_SEP,
  type LayoutOptions,
  getNodeDim,
} from '@/pages/org/layout-shared'
import {
  type DeptSpec,
  ROOT_DEPT_NODE_ID,
  childrenOf,
  layoutOf,
  nodeById,
  orgConfig,
} from '../../helpers/org-layout'

/** The prefs the card renders under, mirrored into the header's row conditions. */
function headerInputsOf(dept: Node, layout: LayoutOptions): DeptHeaderInputs {
  const budgetPercent = dept.data['budgetPercent']
  const statusDots = dept.data['statusDots']
  return {
    showBudgetBar: layout.showBudgetBar ?? false,
    showStatusDots: layout.showStatusDots ?? false,
    showAddAgentButton: layout.showAddAgentButton ?? false,
    budgetPercent: typeof budgetPercent === 'number' ? budgetPercent : null,
    statusDotCount: Array.isArray(statusDots) ? statusDots.length : 0,
    isEmpty: dept.data['isEmpty'] === true,
    isCollapsed: dept.data['isCollapsed'] === true,
  }
}

// `orgConfig` makes each department's first member its head, so engineering is a
// head plus a rank of four reports: the four-into-two-and-two case.
const ORG: readonly DeptSpec[] = [
  { name: 'executive', members: ['zoe', 'cto'] },
  { name: 'product', members: ['pia'] },
  { name: 'engineering', members: ['alice', 'bob', 'carol', 'dave', 'erin'] },
  { name: 'design', members: ['dana', 'dex'] },
  { name: 'quality', members: ['quinn', 'qadir'] },
  { name: 'analytics', members: ['ana'] },
]

/** A department's members other than its head, which sits on its own rank. */
function reportsOf(nodes: readonly Node[], deptId: string): Node[] {
  return childrenOf(nodes, deptId).filter((n) => n.data['isDeptLead'] !== true)
}

/**
 * Every combination of the toggles that add or remove a header row, plus the
 * densities the card padding resolves at. The overlap this file exists for was
 * specific to one combination (the budget bar on), so a single case would have
 * missed it.
 */
const CASES: readonly LayoutOptions[] = [
  {},
  { showBudgetBar: true },
  { showStatusDots: true },
  { showAddAgentButton: true },
  { showBudgetBar: true, showStatusDots: true, showAddAgentButton: true },
  { showBudgetBar: true, showAddAgentButton: true, density: 'dense' },
  { showBudgetBar: true, showAddAgentButton: true, density: 'sparse' },
]

describe('a department card never overlaps its own chrome', () => {
  it.each(CASES)('keeps every agent inside the reserved band (%j)', (layout) => {
    const nodes = layoutOf(orgConfig(ORG), { layout })
    const padding = cardPaddingFor(layout.density)
    for (const dept of nodes.filter((n) => n.type === 'department')) {
      const inputs = headerInputsOf(dept, layout)
      const bandTop = padding + deptHeaderHeight(inputs)
      const bandBottom = getNodeDim(dept).h - padding - deptFooterHeight(inputs)
      for (const child of childrenOf(nodes, dept.id)) {
        // The reported defect, stated as a number: the stats pill row rendered 6
        // px past where the layout said the header ended, so it sat on top of
        // this card. Anything above `bandTop` is that overlap.
        expect(child.position.y).toBeGreaterThanOrEqual(bandTop)
        expect(child.position.y + getNodeDim(child).h).toBeLessThanOrEqual(bandBottom)
      }
    }
  })

  it.each(CASES)('leaves no dead space above the first agent (%j)', (layout) => {
    const nodes = layoutOf(orgConfig(ORG), { layout })
    const padding = cardPaddingFor(layout.density)
    for (const dept of nodes.filter((n) => n.type === 'department')) {
      const children = childrenOf(nodes, dept.id)
      if (children.length === 0) continue
      const bandTop = padding + deptHeaderHeight(headerInputsOf(dept, layout))
      // Reserving MORE than the header needs is the same defect pointing the
      // other way: it reads as a blank strip inside the card.
      expect(Math.min(...children.map((c) => c.position.y))).toBe(bandTop)
    }
  })
})

describe('the chart wraps instead of running off sideways', () => {
  it('lays five departments out in two rows, not one', () => {
    const nodes = layoutOf(orgConfig(ORG))
    const nonRoot = nodes.filter(
      (n) => n.type === 'department' && n.id !== ROOT_DEPT_NODE_ID,
    )
    expect(nonRoot).toHaveLength(5)
    expect(new Set(nonRoot.map((n) => n.position.y)).size).toBe(2)
  })

  it('lays a rank of four agents out two and two', () => {
    const nodes = layoutOf(orgConfig(ORG))
    const reports = reportsOf(nodes, 'dept-engineering')
    expect(reports).toHaveLength(4)
    expect(gridColumnCount(4)).toBe(2)
    expect(new Set(reports.map((a) => a.position.x)).size).toBe(2)
    expect(new Set(reports.map((a) => a.position.y)).size).toBe(2)
  })

  it('keeps the chart from being far wider than it is tall', () => {
    const nodes = layoutOf(orgConfig(ORG))
    const boxes = nodes.filter((n) => n.parentId === undefined)
    const width =
      Math.max(...boxes.map((n) => n.position.x + getNodeDim(n).w))
      - Math.min(...boxes.map((n) => n.position.x))
    const height =
      Math.max(...boxes.map((n) => n.position.y + getNodeDim(n).h))
      - Math.min(...boxes.map((n) => n.position.y))
    // One row of these six departments measured 2700 x 654, an aspect ratio past
    // four. A wrapped chart stays inside the shape of a screen.
    expect(width / height).toBeLessThan(2.5)
  })

  it('narrows a four-report department to the block its reports wrapped into', () => {
    const nodes = layoutOf(orgConfig(ORG))
    const engineering = nodeById(nodes, 'dept-engineering')
    const reports = reportsOf(nodes, 'dept-engineering')
    const cardWidth = getNodeDim(reports[0]!).w
    const oneLine = 4 * cardWidth + 3 * DEFAULT_NODE_SEP
    // Two columns of reports rather than four, so the card is roughly half the
    // width the same department used to demand of the row it sits in.
    expect(getNodeDim(engineering).w).toBeLessThan(oneLine)
    expect(getNodeDim(engineering).w).toBeGreaterThanOrEqual(2 * cardWidth)
  })
})
