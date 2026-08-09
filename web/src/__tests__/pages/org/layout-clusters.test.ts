import { describe, expect, it } from 'vitest'
import type { Node } from '@xyflow/react'
import { chooseClusterDirection } from '@/pages/org/layout-clusters'
import {
  DEFAULT_NODE_SEP,
  DEFAULT_NODE_WIDTH,
  DEPT_HORIZONTAL_WIDTH_BUDGET,
  getNodeDim,
} from '@/pages/org/layout-shared'
import {
  type DeptSpec,
  ROOT_DEPT_NODE_ID,
  agentIds,
  childrenOf,
  fitsInside,
  layoutOf,
  leftToRight,
  nodeById,
  orgConfig,
  overlaps,
  pairsOf,
  topToBottom,
} from '../../helpers/org-layout'

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
  return nodes.filter((n) => n.type === 'agent' && n.parentId === `dept-${deptName}`)
}

describe('chooseClusterDirection', () => {
  it('keeps a department that fits the width budget top-to-bottom', () => {
    expect(chooseClusterDirection(largestFittingRank(DEFAULT_NODE_SEP), DEFAULT_NODE_SEP))
      .toBe('TB')
  })

  it('turns a department that overruns the width budget left-to-right', () => {
    expect(chooseClusterDirection(largestFittingRank(DEFAULT_NODE_SEP) + 1, DEFAULT_NODE_SEP))
      .toBe('LR')
  })

  it('keeps a single-member department top-to-bottom', () => {
    expect(chooseClusterDirection(1, DEFAULT_NODE_SEP)).toBe('TB')
  })

  it('turns fewer members left-to-right as the separation grows', () => {
    expect(largestFittingRank(DEFAULT_NODE_SEP * 2))
      .toBeLessThan(largestFittingRank(DEFAULT_NODE_SEP))
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
    expect(new Set(reports.map((r) => r.position.y)).size).toBe(1)
  })

  it('puts a wide department\'s reports in a column beside its lead', () => {
    const nodes = layoutOf(orgConfig(MIXED_ORG))
    const [lead, ...reports] = membersOf(nodes, 'engineering')
    for (const report of reports) {
      expect(lead!.position.x).toBeLessThan(report.position.x)
    }
    expect(new Set(reports.map((r) => r.position.x)).size).toBe(1)
    expect(new Set(reports.map((r) => r.position.y)).size).toBe(reports.length)
  })

  it('keeps a wide department\'s card inside the width budget', () => {
    const dept = nodeById(layoutOf(orgConfig(MIXED_ORG)), 'dept-engineering')
    expect(dept.width as number).toBeLessThanOrEqual(DEPT_HORIZONTAL_WIDTH_BUDGET)
  })

  it('centres a left-to-right department\'s lead on its reports', () => {
    const nodes = layoutOf(orgConfig(MIXED_ORG))
    const [lead, ...reports] = membersOf(nodes, 'engineering')
    const tops = reports.map((r) => r.position.y)
    const bottoms = reports.map((r) => r.position.y + getNodeDim(r).h)
    const reportsMidpoint = (Math.min(...tops) + Math.max(...bottoms)) / 2
    expect(lead!.position.y + getNodeDim(lead!).h / 2).toBeCloseTo(reportsMidpoint, 5)
  })

  it('leaves the global top-to-bottom flow unchanged', () => {
    const order = ['owner-owner-1', ROOT_DEPT_NODE_ID, 'dept-ops']
    expect(topToBottom(layoutOf(orgConfig(MIXED_ORG)), order)).toEqual(order)
  })

  it('still renders departments in the operator\'s order', () => {
    const order = ['dept-ops', 'dept-engineering']
    expect(leftToRight(layoutOf(orgConfig(MIXED_ORG)), order)).toEqual(order)
  })

  it('still renders a left-to-right department\'s members in the operator\'s order', () => {
    const reports = agentIds(['bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'heidi'])
    expect(topToBottom(layoutOf(orgConfig(MIXED_ORG)), reports)).toEqual(reports)
  })

  it('overlaps no two department cards', () => {
    const departments = layoutOf(orgConfig(MIXED_ORG)).filter((n) => n.type === 'department')
    for (const [a, b] of pairsOf(departments)) {
      expect(overlaps(a, b)).toBe(false)
    }
  })

  it('keeps every member inside its department card', () => {
    const nodes = layoutOf(orgConfig(MIXED_ORG))
    for (const dept of nodes.filter((n) => n.type === 'department')) {
      for (const member of childrenOf(nodes, dept.id)) {
        expect(fitsInside(member, dept)).toBe(true)
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
      for (const [a, b] of pairsOf(siblings)) {
        expect(overlaps(a, b)).toBe(false)
      }
    }
  })
})

describe('separation is the same in every unit', () => {
  const ORG: readonly DeptSpec[] = [
    { name: 'executive', members: ['zoe'] },
    {
      name: 'engineering',
      members: ['alice', 'bob', 'carol', 'dave', 'erin'],
      teams: [{ name: 'core', members: ['bob', 'carol', 'dave'] }],
    },
  ]

  /** Smallest horizontal gap between two cards sharing a rank. */
  function narrowestGap(members: readonly Node[]): number {
    const sameRank = members.filter((m) => m.position.y === members[0]?.position.y)
    const sorted = [...sameRank].sort((a, b) => a.position.x - b.position.x)
    let narrowest = Infinity
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1]!
      narrowest = Math.min(narrowest, sorted[i]!.position.x - (prev.position.x + getNodeDim(prev).w))
    }
    return narrowest
  }

  it('separates team members by the same gap as loose department members', () => {
    const nodes = layoutOf(orgConfig(ORG))
    const teamMembers = childrenOf(nodes, 'team-engineering-core')
      .filter((n) => n.id !== 'agent-bob')
    expect(narrowestGap(teamMembers)).toBeCloseTo(DEFAULT_NODE_SEP, 5)
  })
})
