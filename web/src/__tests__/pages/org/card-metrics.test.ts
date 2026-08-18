import { describe, expect, it } from 'vitest'
import {
  DEPT_HEADER_ROW_GAP,
  DEPT_HEADER_ROW_HEIGHT,
  DEPT_HEADER_TRAILING_GAP,
  type DeptHeaderInputs,
  agentCardSize,
  cardPaddingFor,
  deptFooterHeight,
  deptHeaderContentHeight,
  deptHeaderHeight,
  deptHeaderRows,
} from '@/pages/org/card-metrics'

function inputs(overrides: Partial<DeptHeaderInputs> = {}): DeptHeaderInputs {
  return {
    showBudgetBar: false,
    showStatusDots: false,
    showAddAgentButton: false,
    budgetPercent: null,
    statusDotCount: 0,
    isEmpty: false,
    isCollapsed: false,
    ...overrides,
  }
}

describe('deptHeaderRows', () => {
  it('always shows the title row', () => {
    expect(deptHeaderRows(inputs({ isEmpty: true }))).toContain('title')
  })

  it('shows the stats row on a populated, expanded department', () => {
    expect(deptHeaderRows(inputs())).toEqual(['title', 'stats'])
  })

  it('drops the stats row when the department is collapsed', () => {
    expect(deptHeaderRows(inputs({ isCollapsed: true }))).toEqual(['title'])
  })

  it('drops the stats row when the department is unstaffed', () => {
    expect(deptHeaderRows(inputs({ isEmpty: true }))).toEqual(['title'])
  })

  it('shows the budget row only with both the toggle and an allocation', () => {
    expect(deptHeaderRows(inputs({ showBudgetBar: true, budgetPercent: 20 })))
      .toEqual(['title', 'budget', 'stats'])
    expect(deptHeaderRows(inputs({ showBudgetBar: true, budgetPercent: null })))
      .toEqual(['title', 'stats'])
    expect(deptHeaderRows(inputs({ showBudgetBar: false, budgetPercent: 20 })))
      .toEqual(['title', 'stats'])
  })

  it('does not reserve a budget row for a department allocated nothing', () => {
    // The toggle alone used to decide it, so an unallocated department carried a
    // blank strip inside its header on a chart where the toggle was on.
    expect(deptHeaderRows(inputs({ showBudgetBar: true, budgetPercent: 0 })))
      .toEqual(['title', 'stats'])
  })

  it('shows the dots row only with both the toggle and an agent to dot', () => {
    expect(deptHeaderRows(inputs({ showStatusDots: true, statusDotCount: 3 })))
      .toEqual(['title', 'dots', 'stats'])
    expect(deptHeaderRows(inputs({ showStatusDots: true, statusDotCount: 0 })))
      .toEqual(['title', 'stats'])
  })

  it('keeps the rows in the order the card renders them', () => {
    const every = deptHeaderRows(
      inputs({
        showBudgetBar: true,
        budgetPercent: 10,
        showStatusDots: true,
        statusDotCount: 2,
      }),
    )
    expect(every).toEqual(['title', 'budget', 'dots', 'stats'])
  })
})

describe('deptHeaderHeight', () => {
  it('sums the listed rows and the gaps between them', () => {
    const only = inputs({ isCollapsed: true })
    expect(deptHeaderContentHeight(only)).toBe(DEPT_HEADER_ROW_HEIGHT.title)
  })

  it('counts one gap fewer than it counts rows', () => {
    const two = inputs()
    expect(deptHeaderContentHeight(two)).toBe(
      DEPT_HEADER_ROW_HEIGHT.title + DEPT_HEADER_ROW_GAP + DEPT_HEADER_ROW_HEIGHT.stats,
    )
  })

  it('leaves a gap between the last header row and the first agent card', () => {
    const at = inputs()
    expect(deptHeaderHeight(at) - deptHeaderContentHeight(at)).toBe(
      DEPT_HEADER_TRAILING_GAP,
    )
  })

  it('grows by exactly one row plus its gap when a row is added', () => {
    const without = inputs()
    const with_ = inputs({ showStatusDots: true, statusDotCount: 1 })
    expect(deptHeaderHeight(with_) - deptHeaderHeight(without)).toBe(
      DEPT_HEADER_ROW_HEIGHT.dots + DEPT_HEADER_ROW_GAP,
    )
  })
})

describe('deptFooterHeight', () => {
  it('reserves nothing when the add-agent chip is off', () => {
    expect(deptFooterHeight(inputs())).toBe(0)
  })

  it('reserves the chip band when it is on', () => {
    expect(deptFooterHeight(inputs({ showAddAgentButton: true }))).toBeGreaterThan(0)
  })

  it('reserves nothing on an unstaffed card, whose chip is in the empty state', () => {
    expect(deptFooterHeight(inputs({ showAddAgentButton: true, isEmpty: true }))).toBe(0)
  })
})

describe('agentCardSize', () => {
  it('tracks the density its padding token resolves at', () => {
    const dense = agentCardSize('dense')
    const sparse = agentCardSize('sparse')
    expect(sparse.height - dense.height).toBe(
      (cardPaddingFor('sparse') - cardPaddingFor('dense')) * 2,
    )
  })

  it('keeps one width whatever the density, so sibling centres stay aligned', () => {
    const widths = (['dense', 'medium', 'balanced', 'sparse'] as const).map(
      (density) => agentCardSize(density).width,
    )
    expect(new Set(widths).size).toBe(1)
  })

  it('falls back to the balanced footprint when no density is set', () => {
    // `layout.ts` passes `density` straight from the caller's options, so this
    // path runs whenever the operator's preferences carry none, which is every
    // chart before the preference is first written.
    expect(cardPaddingFor(undefined)).toBe(cardPaddingFor('balanced'))
    expect(agentCardSize(undefined)).toEqual(agentCardSize('balanced'))
  })
})
