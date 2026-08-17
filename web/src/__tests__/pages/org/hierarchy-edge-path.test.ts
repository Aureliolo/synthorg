import { describe, expect, it } from 'vitest'
import {
  hierarchyEdgeCorners,
  hierarchyEdgePath,
  hierarchyPathLength,
  type HierarchyEdgeData,
  type HierarchyEdgeGeometry,
} from '@/pages/org/hierarchy-edge-geometry'

/**
 * The three shapes an edge turns, from its endpoints and its routed corridors.
 *
 * The routing module decides WHERE the corridors are; this decides which corners
 * an edge actually turns to use them, and it is the half a reader of either file
 * alone cannot check.
 */

function props(
  endpoints: { sx: number; sy: number; tx: number; ty: number },
  data?: HierarchyEdgeData,
): HierarchyEdgeGeometry {
  return {
    sourceX: endpoints.sx,
    sourceY: endpoints.sy,
    targetX: endpoints.tx,
    targetY: endpoints.ty,
    data,
  }
}

describe('hierarchyEdgeCorners', () => {
  it('drops to the trunk, runs across, and drops in for a first-row target', () => {
    const corners = hierarchyEdgeCorners(
      props({ sx: 0, sy: 100, tx: 200, ty: 300 }, { trunkY: 200, busY: 200 }),
    )
    expect(corners).toEqual([
      [0, 100],
      [0, 200],
      [200, 200],
      [200, 300],
    ])
  })

  it('takes the riser down past the rows in the way for a later-row target', () => {
    const corners = hierarchyEdgeCorners(
      props(
        { sx: 0, sy: 100, tx: 200, ty: 500 },
        { trunkY: 200, busY: 400, riserX: -150 },
      ),
    )
    expect(corners).toEqual([
      [0, 100],
      [0, 200],
      [-150, 200],
      [-150, 400],
      [200, 400],
      [200, 500],
    ])
  })

  it('drops straight when the target is already under its source', () => {
    // A bend here would emit two zero-length segments and a visible kink.
    const corners = hierarchyEdgeCorners(
      props({ sx: 40, sy: 100, tx: 40, ty: 300 }, { trunkY: 200, busY: 200 }),
    )
    expect(corners).toEqual([
      [40, 100],
      [40, 300],
    ])
  })

  it('ignores a riser whose bus is the trunk it already reached', () => {
    // Same corridor twice would draw a zero-height riser segment.
    const corners = hierarchyEdgeCorners(
      props(
        { sx: 0, sy: 100, tx: 200, ty: 300 },
        { trunkY: 200, busY: 200, riserX: -150 },
      ),
    )
    expect(corners).toHaveLength(4)
    expect(corners).not.toContainEqual([-150, 200])
  })

  it('bends below the source when no routing has been handed to it', () => {
    // Reached before a layout has placed the nodes, and for the owner-to-root
    // edge. Every edge sharing a source Y still bends at the same absolute Y,
    // so siblings coincide into one junction rather than fanning out.
    const first = hierarchyEdgeCorners(props({ sx: 0, sy: 100, tx: 200, ty: 300 }))
    const sibling = hierarchyEdgeCorners(
      props({ sx: 0, sy: 100, tx: -200, ty: 300 }),
    )

    expect(first[1]![1]).toBe(sibling[1]![1])
    expect(first[1]![1]).toBe(130)
  })

  it('falls back to the trunk when only the trunk is routed', () => {
    const corners = hierarchyEdgeCorners(
      props({ sx: 0, sy: 100, tx: 200, ty: 300 }, { trunkY: 250 }),
    )
    expect(corners).toEqual([
      [0, 100],
      [0, 250],
      [200, 250],
      [200, 300],
    ])
  })

  it('turns only right angles', () => {
    const corners = hierarchyEdgeCorners(
      props(
        { sx: 0, sy: 100, tx: 200, ty: 500 },
        { trunkY: 200, busY: 400, riserX: -150 },
      ),
    )
    for (let index = 1; index < corners.length; index++) {
      const [x1, y1] = corners[index - 1]!
      const [x2, y2] = corners[index]!
      expect(x1 === x2 || y1 === y2).toBe(true)
    }
  })

  it('starts at the source handle and ends at the target handle', () => {
    const corners = hierarchyEdgeCorners(
      props(
        { sx: 12, sy: 34, tx: 56, ty: 78 },
        { trunkY: 40, busY: 60, riserX: 20 },
      ),
    )
    expect(corners.at(0)).toEqual([12, 34])
    expect(corners.at(-1)).toEqual([56, 78])
  })
})

describe('hierarchyEdgePath', () => {
  it('moves to the first corner and lines to every other', () => {
    expect(
      hierarchyEdgePath([
        [0, 0],
        [0, 100],
        [50, 100],
      ]),
    ).toBe('M0,0 L0,100 L50,100')
  })
})

describe('hierarchyPathLength', () => {
  it('sums the orthogonal spans, which is the distance drawn', () => {
    // Manhattan, not Euclidean: every segment is axis-aligned, so a particle
    // crossing the path travels exactly this far and its duration is honest.
    expect(
      hierarchyPathLength([
        [0, 0],
        [0, 100],
        [50, 100],
      ]),
    ).toBe(150)
  })

  it('is zero for a path with a single corner', () => {
    expect(hierarchyPathLength([[10, 10]])).toBe(0)
  })
})
