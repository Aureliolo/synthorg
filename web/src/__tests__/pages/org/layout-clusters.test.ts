import { describe, expect, it } from 'vitest'
import type { Node } from '@xyflow/react'
import { chooseClusterDirection } from '@/pages/org/layout-clusters'
import {
  DEFAULT_NODE_WIDTH,
  DEPT_HORIZONTAL_WIDTH_BUDGET,
  getNodeDim,
} from '@/pages/org/layout-shared'
import {
  type DeptSpec,
  ROOT_DEPT_NODE_ID,
  agentIds,
  layoutOf,
  leftToRight,
  nodeById,
  orgConfig,
  topToBottom,
} from '../../helpers/org-layout'

const NODE_SEP = 60

/** Widest rank that still fits the budget at the given separation. */
function largestFittingRank(nodeSep: number): number {
  let count = 1
  while ((count + 1) * (DEFAULT_NODE_WIDTH + nodeSep) - nodeSep <= DEPT_HORIZONTAL_WIDTH_BUDGET) {
    count++
  }
  return count
}

/** Members of one department, in the laid-out order. */
function membersOf(nodes: readonly Node[], deptName: string): Node[] {
  return nodes.filter(
    (n) => n.type === 'agent' && n.parentId === `dept-${deptName}`,
  )
}

function overlaps(a: Node, b: Node): boolean {
  const dimA = getNodeDim(a)
  const dimB = getNodeDim(b)
  return (
    a.position.x < b.position.x + dimB.w
    && b.position.x < a.position.x + dimA.w
    && a.position.y < b.position.y + dimB.h
    && b.position.y < a.position.y + dimA.h
  )
}

describe('chooseClusterDirection', () => {
  it('keeps a department that fits the width budget top-to-bottom', () => {
    expect(chooseClusterDirection(largestFittingRank(NODE_SEP), NODE_SEP)).toBe('TB')
  })

  it('turns a department that overruns the width budget left-to-right', () => {
    expect(chooseClusterDirection(largestFittingRank(NODE_SEP) + 1, NODE_SEP)).toBe('LR')
  })

  it('keeps a single-member department top-to-bottom', () => {
    expect(chooseClusterDirection(1, NODE_SEP)).toBe('TB')
  })

  it('turns fewer members left-to-right as the separation grows', () => {
    expect(largestFittingRank(NODE_SEP * 2)).toBeLessThan(largestFittingRank(NODE_SEP))
  })
})

// A narrow department stays TB; the wide one is well past any plausible budget.
const NARROW: DeptSpec = { name: 'ops', members: ['ola', 'oscar', 'olive'] }
const WIDE: DeptSpec = {
  name: 'engineering',
  members: ['alice', 'bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'heidi'],
}
const MIXED_ORG: readonly DeptSpec[] = [
  { name: 'executive', members: ['zoe'] },
  NARROW,
  WIDE,
]

describe('per-cluster direction', () => {
  it('stacks a narrow department under its lead', () => {
    const nodes = layoutOf(orgConfig(MIXED_ORG))
    const [lead, ...reports] = membersOf(nodes, 'ops')
    for (const report of reports) {
      expect(lead!.position.y).toBeLessThan(report.position.y)
    }
    const ys = new Set(reports.map((r) => r.position.y))
    expect(ys.size).toBe(1)
  })

  it('puts a wide department\'s reports in a column beside its lead', () => {
    const nodes = layoutOf(orgConfig(MIXED_ORG))
    const [lead, ...reports] = membersOf(nodes, 'engineering')
    for (const report of reports) {
      expect(lead!.position.x).toBeLessThan(report.position.x)
    }
    const xs = new Set(reports.map((r) => r.position.x))
    expect(xs.size).toBe(1)
    expect(new Set(reports.map((r) => r.position.y)).size).toBe(reports.length)
  })

  it('keeps a wide department\'s card inside the width budget', () => {
    const nodes = layoutOf(orgConfig(MIXED_ORG))
    const dept = nodeById(nodes, 'dept-engineering')
    expect(dept.width as number).toBeLessThanOrEqual(DEPT_HORIZONTAL_WIDTH_BUDGET)
  })

  it('centres a left-to-right department\'s lead on its reports', () => {
    const nodes = layoutOf(orgConfig(MIXED_ORG))
    const [lead, ...reports] = membersOf(nodes, 'engineering')
    const tops = reports.map((r) => r.position.y)
    const bottoms = reports.map((r) => r.position.y + getNodeDim(r).h)
    const reportsMidpoint = (Math.min(...tops) + Math.max(...bottoms)) / 2
    const leadMidpoint = lead!.position.y + getNodeDim(lead!).h / 2
    expect(leadMidpoint).toBeCloseTo(reportsMidpoint, 5)
  })

  it('leaves the global top-to-bottom flow unchanged', () => {
    const nodes = layoutOf(orgConfig(MIXED_ORG))
    expect(topToBottom(nodes, ['owner-owner-1', ROOT_DEPT_NODE_ID, 'dept-ops'])).toEqual([
      'owner-owner-1',
      ROOT_DEPT_NODE_ID,
      'dept-ops',
    ])
  })

  it('still renders departments in the operator\'s order', () => {
    const nodes = layoutOf(orgConfig(MIXED_ORG))
    expect(leftToRight(nodes, ['dept-ops', 'dept-engineering'])).toEqual([
      'dept-ops',
      'dept-engineering',
    ])
  })

  it('still renders a left-to-right department\'s members in the operator\'s order', () => {
    const nodes = layoutOf(orgConfig(MIXED_ORG))
    const reports = agentIds(['bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'heidi'])
    expect(topToBottom(nodes, reports)).toEqual(reports)
  })

  it('overlaps no two department cards', () => {
    const nodes = layoutOf(orgConfig(MIXED_ORG)).filter((n) => n.type === 'department')
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        expect(overlaps(nodes[i]!, nodes[j]!)).toBe(false)
      }
    }
  })

  it('keeps every member inside its department card', () => {
    const nodes = layoutOf(orgConfig(MIXED_ORG))
    for (const dept of nodes.filter((n) => n.type === 'department')) {
      for (const member of nodes.filter((n) => n.parentId === dept.id)) {
        const { w, h } = getNodeDim(member)
        expect(member.position.x).toBeGreaterThanOrEqual(0)
        expect(member.position.y).toBeGreaterThanOrEqual(0)
        expect(member.position.x + w).toBeLessThanOrEqual(dept.width as number)
        expect(member.position.y + h).toBeLessThanOrEqual(dept.height as number)
      }
    }
  })

  it('overlaps no two cards in a mixed-direction chart', () => {
    const nodes = layoutOf(orgConfig(MIXED_ORG))
    // Compare within one coordinate frame at a time: group children are stored
    // relative to their group, top-level nodes on the canvas.
    const frames = new Map<string, Node[]>()
    for (const node of nodes) {
      if (node.type === 'department') continue
      const frame = node.parentId ?? ''
      frames.set(frame, [...(frames.get(frame) ?? []), node])
    }
    for (const siblings of frames.values()) {
      for (let i = 0; i < siblings.length; i++) {
        for (let j = i + 1; j < siblings.length; j++) {
          expect(overlaps(siblings[i]!, siblings[j]!)).toBe(false)
        }
      }
    }
  })
})
