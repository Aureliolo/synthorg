import { describe, expect, it } from 'vitest'
import {
  flowIntoGrid,
  gridColumnCount,
  type GridBox,
} from '@/pages/org/layout-grid'

function boxes(count: number, w = 100, h = 40): GridBox[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `box-${index}`,
    w,
    h,
  }))
}

const NO_GAP = { gapX: 0, gapY: 0 }

describe('gridColumnCount', () => {
  it.each([
    [0, 0],
    [1, 1],
    [2, 2],
    [3, 2],
    [4, 2],
    [5, 3],
    [9, 3],
    [10, 4],
    [16, 4],
  ])('lays %i boxes out in %i columns', (count, expected) => {
    expect(gridColumnCount(count)).toBe(expected)
  })

  it('never asks for more columns than there are boxes', () => {
    for (let count = 0; count <= 20; count++) {
      expect(gridColumnCount(count)).toBeLessThanOrEqual(Math.max(count, 0))
    }
  })
})

describe('flowIntoGrid', () => {
  it('places nothing for an empty set', () => {
    const result = flowIntoGrid([], NO_GAP)
    expect(result).toEqual({
      placements: [],
      width: 0,
      height: 0,
      columnCount: 0,
      rowCount: 0,
    })
  })

  it('wraps four equal boxes into two rows of two', () => {
    const result = flowIntoGrid(boxes(4), { gapX: 10, gapY: 20 })

    expect(result.columnCount).toBe(2)
    expect(result.rowCount).toBe(2)
    expect(result.placements.map((p) => [p.x, p.y])).toEqual([
      [0, 0],
      [110, 0],
      [0, 60],
      [110, 60],
    ])
  })

  it('fills row by row in the order handed in', () => {
    // The order is the operator's own, so a block that reshuffled it would stop
    // agreeing with the Org Edit page.
    const result = flowIntoGrid(boxes(5), NO_GAP)
    expect(result.placements.map((p) => p.id)).toEqual([
      'box-0',
      'box-1',
      'box-2',
      'box-3',
      'box-4',
    ])
  })

  it('sizes each column to its widest member and each row to its tallest', () => {
    const result = flowIntoGrid(
      [
        { id: 'a', w: 100, h: 40 },
        { id: 'b', w: 300, h: 40 },
        { id: 'c', w: 100, h: 90 },
        { id: 'd', w: 100, h: 40 },
      ],
      NO_GAP,
    )

    // Column 1 is 300 wide because of `b`; row 1 is 90 tall because of `c`.
    expect(result.width).toBe(400)
    expect(result.height).toBe(130)
  })

  it('centres a narrow box in its column so the connector lands on centre', () => {
    const result = flowIntoGrid(
      [
        { id: 'narrow', w: 100, h: 40 },
        { id: 'filler', w: 100, h: 40 },
        { id: 'wide', w: 300, h: 40 },
        { id: 'filler-2', w: 100, h: 40 },
      ],
      NO_GAP,
    )
    const placed = new Map(result.placements.map((p) => [p.id, p]))

    // Column 0 is 300 wide (from `wide`), so the 100-wide box sits at +100.
    expect(placed.get('narrow')!.x).toBe(100)
    expect(placed.get('wide')!.x).toBe(0)
  })

  it('never overlaps two boxes', () => {
    const result = flowIntoGrid(
      [
        { id: 'a', w: 100, h: 40 },
        { id: 'b', w: 300, h: 90 },
        { id: 'c', w: 100, h: 40 },
        { id: 'd', w: 200, h: 120 },
        { id: 'e', w: 100, h: 40 },
      ],
      { gapX: 8, gapY: 12 },
    )
    const sizes = new Map(
      [
        { id: 'a', w: 100, h: 40 },
        { id: 'b', w: 300, h: 90 },
        { id: 'c', w: 100, h: 40 },
        { id: 'd', w: 200, h: 120 },
        { id: 'e', w: 100, h: 40 },
      ].map((box) => [box.id, box]),
    )

    for (const first of result.placements) {
      for (const second of result.placements) {
        if (first.id === second.id) continue
        const a = sizes.get(first.id)!
        const b = sizes.get(second.id)!
        const disjoint =
          first.x + a.w <= second.x
          || second.x + b.w <= first.x
          || first.y + a.h <= second.y
          || second.y + b.h <= first.y
        expect(disjoint).toBe(true)
      }
    }
  })

  it('reports a footprint that contains every box', () => {
    const boxSet = [
      { id: 'a', w: 100, h: 40 },
      { id: 'b', w: 300, h: 90 },
      { id: 'c', w: 100, h: 40 },
    ]
    const result = flowIntoGrid(boxSet, { gapX: 8, gapY: 12 })
    const sizes = new Map(boxSet.map((box) => [box.id, box]))

    for (const placement of result.placements) {
      const box = sizes.get(placement.id)!
      expect(placement.x + box.w).toBeLessThanOrEqual(result.width)
      expect(placement.y + box.h).toBeLessThanOrEqual(result.height)
    }
  })

  it('lays a single box out at the origin with its own footprint', () => {
    const result = flowIntoGrid([{ id: 'only', w: 240, h: 90 }], {
      gapX: 40,
      gapY: 40,
    })

    expect(result.placements).toEqual([{ id: 'only', x: 0, y: 0 }])
    // No trailing gap: a one-box block is exactly the box.
    expect(result.width).toBe(240)
    expect(result.height).toBe(90)
  })
})
