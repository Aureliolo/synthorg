import { describe, expect, it } from 'vitest'
import fc from 'fast-check'
import { findDropTarget, type DepartmentBounds } from '@/pages/org/drop-target'

function makeDept(
  name: string,
  x: number,
  y: number,
  width: number,
  height: number,
): DepartmentBounds {
  return { departmentName: name, nodeId: `dept-${name}`, x, y, width, height }
}

describe('findDropTarget', () => {
  it('returns null for empty departments list', () => {
    expect(findDropTarget({ x: 50, y: 50 }, [])).toBeNull()
  })

  it('returns the department when point is inside', () => {
    const depts = [makeDept('engineering', 0, 0, 100, 100)]
    const result = findDropTarget({ x: 50, y: 50 }, depts)
    expect(result).not.toBeNull()
    expect(result!.departmentName).toBe('engineering')
  })

  it('returns null when point is outside all departments', () => {
    const depts = [makeDept('engineering', 0, 0, 100, 100)]
    expect(findDropTarget({ x: 200, y: 200 }, depts)).toBeNull()
  })

  it('returns department when point is on the boundary (inclusive)', () => {
    const depts = [makeDept('engineering', 0, 0, 100, 100)]
    // Top-left corner
    expect(findDropTarget({ x: 0, y: 0 }, depts)).not.toBeNull()
    // Bottom-right corner
    expect(findDropTarget({ x: 100, y: 100 }, depts)).not.toBeNull()
    // Right edge
    expect(findDropTarget({ x: 100, y: 50 }, depts)).not.toBeNull()
    // Bottom edge
    expect(findDropTarget({ x: 50, y: 100 }, depts)).not.toBeNull()
  })

  it('returns the correct department among multiple', () => {
    const depts = [
      makeDept('engineering', 0, 0, 100, 100),
      makeDept('design', 200, 0, 100, 100),
      makeDept('product', 0, 200, 100, 100),
    ]
    expect(findDropTarget({ x: 50, y: 50 }, depts)!.departmentName).toBe('engineering')
    expect(findDropTarget({ x: 250, y: 50 }, depts)!.departmentName).toBe('design')
    expect(findDropTarget({ x: 50, y: 250 }, depts)!.departmentName).toBe('product')
  })

  it('returns the smallest area department when overlapping', () => {
    const depts = [
      makeDept('engineering', 0, 0, 300, 300), // large
      makeDept('design', 50, 50, 100, 100), // small, nested inside large
    ]
    const result = findDropTarget({ x: 75, y: 75 }, depts)
    expect(result!.departmentName).toBe('design')
  })

  it('handles negative coordinates', () => {
    const depts = [makeDept('engineering', -100, -100, 200, 200)]
    expect(findDropTarget({ x: -50, y: -50 }, depts)!.departmentName).toBe('engineering')
    expect(findDropTarget({ x: 0, y: 0 }, depts)!.departmentName).toBe('engineering')
    expect(findDropTarget({ x: 150, y: 0 }, depts)).toBeNull()
  })

  describe('properties', () => {
    it('point inside generated rect is always found', () => {
      fc.assert(
        fc.property(
          fc.record({
            x: fc.integer({ min: -1000, max: 1000 }),
            y: fc.integer({ min: -1000, max: 1000 }),
            w: fc.integer({ min: 1, max: 500 }),
            h: fc.integer({ min: 1, max: 500 }),
          }),
          ({ x, y, w, h }) => {
            const dept = makeDept('test', x, y, w, h)
            // Point at center of rect
            const cx = x + w / 2
            const cy = y + h / 2
            const result = findDropTarget({ x: cx, y: cy }, [dept])
            expect(result).not.toBeNull()
          },
        ),
      )
    })

    it('point far outside any rect returns null', () => {
      fc.assert(
        fc.property(
          fc.record({
            x: fc.integer({ min: 0, max: 100 }),
            y: fc.integer({ min: 0, max: 100 }),
            w: fc.integer({ min: 1, max: 50 }),
            h: fc.integer({ min: 1, max: 50 }),
          }),
          ({ x, y, w, h }) => {
            const dept = makeDept('test', x, y, w, h)
            // Point far to the right of the rect
            const result = findDropTarget({ x: x + w + 1000, y: y + h + 1000 }, [dept])
            expect(result).toBeNull()
          },
        ),
      )
    })
  })
})
