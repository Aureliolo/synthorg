import { describe, expect, it } from 'vitest'
import type { Edge, Node } from '@xyflow/react'
import { chooseClusterDirection, liftEdges } from '@/pages/org/layout-clusters'
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

/** What a rank of `count` default-width cards measures at the given separation. */
function rankWidth(count: number, nodeSep: number): number {
  return count * DEFAULT_NODE_WIDTH + (count - 1) * nodeSep
}

/** Most default-width cards that still fit the budget at the given separation. */
function largestFittingRank(nodeSep: number): number {
  let count = 1
  while (rankWidth(count + 1, nodeSep) <= DEPT_HORIZONTAL_WIDTH_BUDGET) count++
  return count
}

/** The department's own members, in the laid-out order, lead first. */
function membersOf(nodes: readonly Node[], deptName: string): [Node, Node[]] {
  const members = nodes.filter((n) => n.type === 'agent' && n.parentId === `dept-${deptName}`)
  // The lead is found by its flag, not by list position: `findDeptHead`
  // resolves it from the department's declared head role and does not reorder
  // the member array, so an operator whose head is not listed first would
  // otherwise make every one of these cases assert against the wrong node.
  const lead = members.find((n) => n.data['isDeptLead'] === true)
  if (!lead) throw new Error(`department ${deptName} has no lead node`)
  return [lead, members.filter((n) => n.id !== lead.id)]
}

describe('chooseClusterDirection', () => {
  it('keeps a department that fits the width budget top-to-bottom', () => {
    const fitting = rankWidth(largestFittingRank(DEFAULT_NODE_SEP), DEFAULT_NODE_SEP)
    expect(chooseClusterDirection(fitting)).toBe('TB')
  })

  it('turns a department that overruns the width budget left-to-right', () => {
    const overrun = rankWidth(largestFittingRank(DEFAULT_NODE_SEP) + 1, DEFAULT_NODE_SEP)
    expect(chooseClusterDirection(overrun)).toBe('LR')
  })

  it('keeps a single-member department top-to-bottom', () => {
    expect(chooseClusterDirection(rankWidth(1, DEFAULT_NODE_SEP))).toBe('TB')
  })

  it('turns the same members left-to-right once the separation grows', () => {
    const count = largestFittingRank(DEFAULT_NODE_SEP)
    expect(chooseClusterDirection(rankWidth(count, DEFAULT_NODE_SEP))).toBe('TB')
    expect(chooseClusterDirection(rankWidth(count, DEFAULT_NODE_SEP * 2))).toBe('LR')
  })

  it('turns a rank of wide boxes left-to-right on a count that would fit', () => {
    // Three team boxes are three members, which the budget accommodates as
    // plain cards; at their rendered width the same rank overruns it.
    const asCards = rankWidth(3, DEFAULT_NODE_SEP)
    expect(chooseClusterDirection(asCards)).toBe('TB')
    expect(chooseClusterDirection(3 * 400 + 2 * DEFAULT_NODE_SEP)).toBe('LR')
  })
})

function edge(id: string, source: string, target: string, data?: Edge['data']): Edge {
  return data === undefined ? { id, source, target } : { id, source, target, data }
}

/**
 * The hidden cross-department edges exist to give the layout a rank
 * relationship, and `build-org-tree` emits each one right after the visible
 * edge between the same two boxes. Lifting collapses both onto one top-level
 * pair, so whatever a hidden edge carries never reaches dagre; a rank distance
 * keyed off its tag would be applied to nothing. Pinned here because the tag
 * still exists and reads like it is load-bearing.
 */
describe('liftEdges', () => {
  it('collapses an owner-to-root hidden edge into its visible counterpart', () => {
    const lifted = liftEdges(
      [
        edge('visible', 'owner-1', 'dept-executive'),
        edge('hidden', 'owner-1', 'agent-zoe', { crossDeptKind: 'owner-to-root' }),
      ],
      new Map([['agent-zoe', 'dept-executive']]),
    )
    expect(lifted).toHaveLength(1)
    expect(lifted[0]!.id).toBe('visible')
    expect(lifted[0]!.data).toBeUndefined()
  })

  it('collapses a ceo-to-child hidden edge into its visible counterpart', () => {
    const lifted = liftEdges(
      [
        edge('visible', 'dept-executive', 'dept-ops'),
        edge('hidden', 'agent-zoe', 'agent-ola', { crossDeptKind: 'ceo-to-child' }),
      ],
      new Map([['agent-zoe', 'dept-executive'], ['agent-ola', 'dept-ops']]),
    )
    expect(lifted).toHaveLength(1)
    expect(lifted[0]!.id).toBe('visible')
    expect(lifted[0]!.data).toBeUndefined()
  })

  it('drops an edge whose endpoints lift into the same box', () => {
    const lifted = liftEdges(
      [edge('within', 'agent-zoe', 'agent-zed')],
      new Map([['agent-zoe', 'dept-executive'], ['agent-zed', 'dept-executive']]),
    )
    expect(lifted).toEqual([])
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
    const [lead, reports] = membersOf(layoutOf(orgConfig(MIXED_ORG)), 'ops')
    for (const report of reports) {
      expect(lead.position.y).toBeLessThan(report.position.y)
    }
    expect(new Set(reports.map((r) => r.position.y)).size).toBe(1)
  })

  it('puts a wide department\'s reports in a column beside its lead', () => {
    const [lead, reports] = membersOf(layoutOf(orgConfig(MIXED_ORG)), 'engineering')
    for (const report of reports) {
      expect(lead.position.x).toBeLessThan(report.position.x)
    }
    expect(new Set(reports.map((r) => r.position.x)).size).toBe(1)
    expect(new Set(reports.map((r) => r.position.y)).size).toBe(reports.length)
  })

  it('keeps a wide department\'s card inside the width budget', () => {
    const dept = nodeById(layoutOf(orgConfig(MIXED_ORG)), 'dept-engineering')
    expect(getNodeDim(dept).w).toBeLessThanOrEqual(DEPT_HORIZONTAL_WIDTH_BUDGET)
  })

  it('centres a left-to-right department\'s lead on its reports', () => {
    const [lead, reports] = membersOf(layoutOf(orgConfig(MIXED_ORG)), 'engineering')
    const tops = reports.map((r) => r.position.y)
    const bottoms = reports.map((r) => r.position.y + getNodeDim(r).h)
    const reportsMidpoint = (Math.min(...tops) + Math.max(...bottoms)) / 2
    expect(lead.position.y + getNodeDim(lead).h / 2).toBeCloseTo(reportsMidpoint, 5)
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
