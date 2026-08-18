import { describe, expect, it } from 'vitest'
import type { Edge, Node } from '@xyflow/react'
import {
  applyHierarchyRouting,
  hierarchyRoutingPlan,
  type HierarchyRouting,
} from '@/pages/org/route-hierarchy'
import { getNodeDim } from '@/pages/org/layout-shared'
import { type DeptSpec, layoutOf, orgConfig } from '../../helpers/org-layout'

/** Plan then apply, which is how the chart hook uses the two halves. */
function routeHierarchyEdges(nodes: readonly Node[], edges: readonly Edge[]): Edge[] {
  return applyHierarchyRouting(edges, hierarchyRoutingPlan(nodes, edges))
}

function box(id: string, x: number, y: number, w = 100, h = 40): Node {
  return { id, position: { x, y }, width: w, height: h, data: {} }
}

function hierarchyEdge(source: string, target: string): Edge {
  return { id: `${source}-${target}`, source, target, type: 'hierarchy' }
}

function routingOf(edges: readonly Edge[], source: string, target: string): HierarchyRouting {
  const found = edges.find((e) => e.source === source && e.target === target)
  if (!found) throw new Error(`no edge ${source} -> ${target}`)
  return found.data as HierarchyRouting
}

describe('routeHierarchyEdges', () => {
  it('puts one corridor between the source and its first row of targets', () => {
    const nodes = [box('root', 0, 0), box('a', -200, 200), box('b', 200, 200)]
    const routed = routeHierarchyEdges(nodes, [
      hierarchyEdge('root', 'a'),
      hierarchyEdge('root', 'b'),
    ])
    const a = routingOf(routed, 'root', 'a')
    const b = routingOf(routed, 'root', 'b')
    // Both siblings share it, which is what makes them read as one trunk.
    expect(a.trunkY).toBe(b.trunkY)
    expect(a.trunkY).toBe(120)
    expect(a.busY).toBe(a.trunkY)
    expect(a.riserX).toBeUndefined()
  })

  it('gives a second row its own bus between the rows', () => {
    const nodes = [
      box('root', 0, 0),
      box('a', -200, 200),
      box('b', 200, 200),
      box('c', -200, 400),
    ]
    const routed = routeHierarchyEdges(nodes, [
      hierarchyEdge('root', 'a'),
      hierarchyEdge('root', 'b'),
      hierarchyEdge('root', 'c'),
    ])
    const second = routingOf(routed, 'root', 'c')
    expect(second.busY).toBe(320)
    expect(second.busY).toBeGreaterThan(second.trunkY)
    expect(second.riserX).toBeDefined()
  })

  it('drops the riser between the cards of the row it passes, never across one', () => {
    const nodes = [
      box('root', 0, 0),
      box('a', -200, 200),
      box('b', 100, 200),
      box('c', -200, 400),
    ]
    const routed = routeHierarchyEdges(nodes, [
      hierarchyEdge('root', 'a'),
      hierarchyEdge('root', 'b'),
      hierarchyEdge('root', 'c'),
    ])
    const riserX = routingOf(routed, 'root', 'c').riserX!
    // The gap between a's right edge (-100) and b's left edge (100).
    expect(riserX).toBeGreaterThan(-100)
    expect(riserX).toBeLessThan(100)
  })

  it('takes the riser round the outside when the row has no gap to use', () => {
    const nodes = [
      box('root', 0, 0),
      box('a', -50, 200),
      box('b', 50, 200),
      box('c', 0, 400),
    ]
    const routed = routeHierarchyEdges(nodes, [
      hierarchyEdge('root', 'a'),
      hierarchyEdge('root', 'b'),
      hierarchyEdge('root', 'c'),
    ])
    const riserX = routingOf(routed, 'root', 'c').riserX!
    // The two boxes touch, so the only clear corridors are the flanks.
    expect(riserX <= -50 || riserX >= 150).toBe(true)
  })

  it('keeps siblings that overlap vertically on one row despite unequal tops', () => {
    const nodes = [
      box('root', 0, 0),
      box('tall', -200, 200, 100, 200),
      box('short', 200, 260, 100, 40),
    ]
    const routed = routeHierarchyEdges(nodes, [
      hierarchyEdge('root', 'tall'),
      hierarchyEdge('root', 'short'),
    ])
    const tall = routingOf(routed, 'root', 'tall')
    const short = routingOf(routed, 'root', 'short')
    expect(short.busY).toBe(tall.busY)
    // A phantom second row would put `short`'s bus at y=350, inside `tall`.
    expect(short.riserX).toBeUndefined()
  })

  it('measures a row from its whole extent, not from its leftmost card', () => {
    const nodes = [
      box('root', 0, 0),
      box('a', -300, 200),
      box('b', 0, 200, 100, 120),
      box('c', -300, 400),
      box('d', 0, 340, 100, 80),
    ]
    const routed = routeHierarchyEdges(
      nodes,
      ['a', 'b', 'c', 'd'].map((target) => hierarchyEdge('root', target)),
    )
    const second = routingOf(routed, 'root', 'c')
    // `c` is the leftmost of the second row but `d` starts it, so the corridor
    // belongs above d's top of 340, not above c's of 400.
    expect(second.busY).toBe(330)
    expect(routingOf(routed, 'root', 'd').busY).toBe(second.busY)
  })

  it('leaves an edge with no resolvable target untouched', () => {
    const routed = routeHierarchyEdges([box('root', 0, 0)], [hierarchyEdge('root', 'gone')])
    expect(routed[0]!.data).toBeUndefined()
  })

  it('ignores a hidden edge and a non-hierarchy edge', () => {
    const nodes = [box('root', 0, 0), box('a', 0, 200)]
    const routed = routeHierarchyEdges(nodes, [
      { ...hierarchyEdge('root', 'a'), hidden: true },
      { id: 'comm', source: 'root', target: 'a', type: 'communication' },
    ])
    for (const edge of routed) expect(edge.data).toBeUndefined()
  })

  it('terminates on a parentId cycle instead of spinning', () => {
    const nodes: Node[] = [
      { ...box('a', 0, 0), parentId: 'b' },
      { ...box('b', 0, 0), parentId: 'a' },
    ]
    expect(() => routeHierarchyEdges(nodes, [hierarchyEdge('a', 'b')])).not.toThrow()
  })
})

describe('routing a real chart', () => {
  const ORG: readonly DeptSpec[] = [
    { name: 'executive', members: ['zoe', 'cto'] },
    { name: 'product', members: ['pia'] },
    { name: 'engineering', members: ['alice', 'bob'] },
    { name: 'design', members: ['dana'] },
    { name: 'quality', members: ['quinn'] },
    { name: 'analytics', members: ['ana'] },
  ]

  /** Absolute boxes, so a child's own frame does not skew the comparison. */
  function absolute(
    nodes: readonly Node[],
  ): Map<string, { left: number; right: number; top: number; bottom: number }> {
    const byId = new Map(nodes.map((n) => [n.id, n]))
    const out = new Map<
      string,
      { left: number; right: number; top: number; bottom: number }
    >()
    for (const node of nodes) {
      let x = node.position.x
      let y = node.position.y
      let parent = node.parentId
      while (parent !== undefined) {
        const ancestor = byId.get(parent)
        if (!ancestor) break
        x += ancestor.position.x
        y += ancestor.position.y
        parent = ancestor.parentId
      }
      const dim = getNodeDim(node)
      out.set(node.id, {
        left: x,
        right: x + dim.w,
        top: y,
        bottom: y + dim.h,
      })
    }
    return out
  }

  it('never routes a riser through a department card', () => {
    const tree = orgConfig(ORG)
    const nodes = layoutOf(tree)
    const departments = nodes.filter((n) => n.type === 'department')
    const boxes = absolute(nodes)
    const routed = routeHierarchyEdges(
      nodes,
      departments
        .filter((d) => d.id !== 'dept-executive')
        .map((d) => hierarchyEdge('dept-executive', d.id)),
    )
    const risers = routed
      .map((e) => (e.data as HierarchyRouting | undefined)?.riserX)
      .filter((x): x is number => x !== undefined)
    // The departments wrap onto two rows, so at least one edge needs a riser.
    expect(risers.length).toBeGreaterThan(0)
    for (const riserX of risers) {
      for (const dept of departments) {
        const at = boxes.get(dept.id)!
        expect(riserX > at.left && riserX < at.right).toBe(false)
      }
    }
  })

  it('never routes a trunk or a bus through a department card', () => {
    // The riser is the corridor most likely to cross something, and asserting
    // only it left the other two spans resting on "they sit in a gutter by
    // construction". A horizontal corridor clears a card when its y is outside
    // the card's own band, and that is checkable directly.
    const nodes = layoutOf(orgConfig(ORG))
    const departments = nodes.filter((n) => n.type === 'department')
    const boxes = absolute(nodes)
    const routed = routeHierarchyEdges(
      nodes,
      departments
        .filter((d) => d.id !== 'dept-executive')
        .map((d) => hierarchyEdge('dept-executive', d.id)),
    )
    const corridors = routed.flatMap((edge) => {
      const plan = edge.data as HierarchyRouting | undefined
      return plan === undefined ? [] : [plan.trunkY, plan.busY]
    })
    expect(corridors.length).toBeGreaterThan(0)
    for (const y of corridors) {
      for (const dept of departments) {
        const at = boxes.get(dept.id)!
        expect(y > at.top && y < at.bottom).toBe(false)
      }
    }
  })

  it('gives every department on one row the same bus', () => {
    const nodes = layoutOf(orgConfig(ORG))
    const departments = nodes.filter(
      (n) => n.type === 'department' && n.id !== 'dept-executive',
    )
    const routed = routeHierarchyEdges(
      nodes,
      departments.map((d) => hierarchyEdge('dept-executive', d.id)),
    )
    const busByRow = new Map<number, Set<number>>()
    for (const dept of departments) {
      const plan = routingOf(routed, 'dept-executive', dept.id)
      const row = Math.round(dept.position.y)
      busByRow.set(row, (busByRow.get(row) ?? new Set()).add(plan.busY))
    }
    expect(busByRow.size).toBe(2)
    for (const buses of busByRow.values()) expect(buses.size).toBe(1)
  })
})
