import { describe, expect, it } from 'vitest'
import type { Node, Edge } from '@xyflow/react'
import { applyDagreLayout } from '@/pages/org/layout'

function makeNode(id: string, opts: Partial<Node> = {}): Node {
  return {
    id,
    position: { x: 0, y: 0 },
    data: {},
    ...opts,
  }
}

function makeEdge(source: string, target: string): Edge {
  return { id: `e-${source}-${target}`, source, target }
}

describe('applyDagreLayout', () => {
  it('returns empty array for empty input', () => {
    const result = applyDagreLayout([], [])
    expect(result).toEqual([])
  })

  it('assigns numeric x/y positions to all nodes', () => {
    const nodes = [makeNode('a'), makeNode('b'), makeNode('c')]
    const edges = [makeEdge('a', 'b'), makeEdge('a', 'c')]
    const result = applyDagreLayout(nodes, edges)

    for (const node of result) {
      expect(typeof node.position.x).toBe('number')
      expect(typeof node.position.y).toBe('number')
      expect(Number.isFinite(node.position.x)).toBe(true)
      expect(Number.isFinite(node.position.y)).toBe(true)
    }
  })

  it('positions a single node at a valid position', () => {
    const result = applyDagreLayout([makeNode('solo')], [])
    expect(result).toHaveLength(1)
    expect(typeof result[0]!.position.x).toBe('number')
    expect(typeof result[0]!.position.y).toBe('number')
  })

  it('does not overlap sibling nodes', () => {
    const nodes = [makeNode('parent'), makeNode('child1'), makeNode('child2')]
    const edges = [makeEdge('parent', 'child1'), makeEdge('parent', 'child2')]
    const result = applyDagreLayout(nodes, edges)

    const child1 = result.find((n) => n.id === 'child1')!
    const child2 = result.find((n) => n.id === 'child2')!

    // Children should be separated (different x or different y)
    const samePosition = child1.position.x === child2.position.x && child1.position.y === child2.position.y
    expect(samePosition).toBe(false)
  })

  it('places parent above children in TB direction', () => {
    const nodes = [makeNode('parent'), makeNode('child')]
    const edges = [makeEdge('parent', 'child')]
    const result = applyDagreLayout(nodes, edges, { direction: 'TB' })

    const parent = result.find((n) => n.id === 'parent')!
    const child = result.find((n) => n.id === 'child')!
    expect(parent.position.y).toBeLessThan(child.position.y)
  })

  it('handles department group nodes separately from dagre layout', () => {
    const nodes = [
      makeNode('dept-eng', { type: 'department' }),
      makeNode('agent-1', { parentId: 'dept-eng' }),
      makeNode('agent-2', { parentId: 'dept-eng' }),
    ]
    const edges = [makeEdge('agent-1', 'agent-2')]
    const result = applyDagreLayout(nodes, edges)

    // All nodes should have positions
    expect(result).toHaveLength(3)
    const dept = result.find((n) => n.id === 'dept-eng')!
    expect(typeof dept.position.x).toBe('number')
  })

  it('sizes department groups to contain their children', () => {
    const nodes = [
      makeNode('dept-eng', { type: 'department' }),
      makeNode('a1', { parentId: 'dept-eng' }),
      makeNode('a2', { parentId: 'dept-eng' }),
    ]
    const edges = [makeEdge('a1', 'a2')]
    const result = applyDagreLayout(nodes, edges)

    const dept = result.find((n) => n.id === 'dept-eng')!
    const style = dept.style as { width: number; height: number }
    expect(style.width).toBeGreaterThan(0)
    expect(style.height).toBeGreaterThan(0)
  })

  it('handles edges referencing non-existent nodes gracefully', () => {
    const nodes = [makeNode('a')]
    const edges = [makeEdge('a', 'nonexistent')]
    // Should not throw
    const result = applyDagreLayout(nodes, edges)
    expect(result).toHaveLength(1)
  })

  it('centers a dept lead horizontally over its reports', () => {
    // Three-report department with a flagged lead.  After dagre,
    // dagre tends to put the lead at the leftmost slot of the row;
    // the post-pass should re-anchor it to the midpoint of the
    // reports' bounding box so the head -> report connectors form a
    // clean T-junction.
    const nodes = [
      makeNode('dept-eng', { type: 'department' }),
      makeNode('lead', {
        parentId: 'dept-eng',
        data: { isDeptLead: true },
        width: 200,
        height: 80,
      }),
      makeNode('r1', { parentId: 'dept-eng', width: 200, height: 80 }),
      makeNode('r2', { parentId: 'dept-eng', width: 200, height: 80 }),
      makeNode('r3', { parentId: 'dept-eng', width: 200, height: 80 }),
    ]
    const edges = [
      makeEdge('lead', 'r1'),
      makeEdge('lead', 'r2'),
      makeEdge('lead', 'r3'),
    ]
    const result = applyDagreLayout(nodes, edges)

    const lead = result.find((n) => n.id === 'lead')!
    const reports = ['r1', 'r2', 'r3'].map(
      (id) => result.find((n) => n.id === id)!,
    )

    const reportsXMin = Math.min(...reports.map((r) => r.position.x))
    const reportsXMax = Math.max(
      ...reports.map((r) => r.position.x + (r.width as number)),
    )
    const reportsMidpoint = (reportsXMin + reportsXMax) / 2
    const leadMidpoint = lead.position.x + (lead.width as number) / 2

    expect(Math.abs(reportsMidpoint - leadMidpoint)).toBeLessThan(1)
  })

  it('leaves the lead alone when there are no reports', () => {
    const nodes = [
      makeNode('dept-eng', { type: 'department' }),
      makeNode('solo-lead', {
        parentId: 'dept-eng',
        data: { isDeptLead: true },
        width: 200,
        height: 80,
      }),
    ]
    const result = applyDagreLayout(nodes, [])
    const lead = result.find((n) => n.id === 'solo-lead')!
    expect(typeof lead.position.x).toBe('number')
    expect(Number.isFinite(lead.position.x)).toBe(true)
  })
})
