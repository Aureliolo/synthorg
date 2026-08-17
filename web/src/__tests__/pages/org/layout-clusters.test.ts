import { describe, expect, it } from 'vitest'
import type { Edge, Node } from '@xyflow/react'
import { liftEdges } from '@/pages/org/layout-clusters'
import { gridColumnCount } from '@/pages/org/layout-grid'
import {
  DEFAULT_NODE_SEP,
  DEFAULT_NODE_WIDTH,
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

/** The nodes grouped into their laid-out rows, each row ordered left to right. */
function rowsOf(nodes: readonly Node[]): Node[][] {
  const byRow = new Map<number, Node[]>()
  for (const node of nodes) {
    const key = Math.round(node.position.y)
    byRow.set(key, [...(byRow.get(key) ?? []), node])
  }
  return [...byRow.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([, row]) => [...row].sort((a, b) => a.position.x - b.position.x))
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

describe('gridColumnCount', () => {
  it('leaves one box on its own', () => {
    expect(gridColumnCount(1)).toBe(1)
  })

  it('keeps two boxes side by side', () => {
    expect(gridColumnCount(2)).toBe(2)
  })

  it('wraps four boxes into two and two, which is the whole point', () => {
    expect(gridColumnCount(4)).toBe(2)
  })

  it('grows the column count as the square root of the box count', () => {
    expect([3, 5, 9, 10, 16, 17].map(gridColumnCount)).toEqual([2, 3, 3, 4, 4, 5])
  })

  it('never asks for more columns than there are boxes', () => {
    for (let count = 0; count <= 40; count++) {
      expect(gridColumnCount(count)).toBeLessThanOrEqual(Math.max(count, 0))
    }
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

// The narrow department's reports fit one line; the wide one's cannot.
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

describe('a wide rank wraps into a block', () => {
  it('keeps a rank that fits on one line', () => {
    const [lead, reports] = membersOf(layoutOf(orgConfig(MIXED_ORG)), 'ops')
    for (const report of reports) {
      expect(lead.position.y).toBeLessThan(report.position.y)
    }
    expect(new Set(reports.map((r) => r.position.y)).size).toBe(1)
  })

  it('wraps a wide department\'s reports into a block under its lead', () => {
    const [lead, reports] = membersOf(layoutOf(orgConfig(MIXED_ORG)), 'engineering')
    for (const report of reports) {
      expect(lead.position.y).toBeLessThan(report.position.y)
    }
    const rows = rowsOf(reports)
    expect(rows).toHaveLength(3)
    for (const row of rows) {
      expect(row.length).toBeLessThanOrEqual(gridColumnCount(reports.length))
    }
  })

  it('keeps a wide department\'s card narrower than the line it replaced', () => {
    const dept = nodeById(layoutOf(orgConfig(MIXED_ORG)), 'dept-engineering')
    expect(getNodeDim(dept).w).toBeLessThan(rankWidth(7, DEFAULT_NODE_SEP))
  })

  it('centres a wide department\'s lead over the block of its reports', () => {
    const [lead, reports] = membersOf(layoutOf(orgConfig(MIXED_ORG)), 'engineering')
    const lefts = reports.map((r) => r.position.x)
    const rights = reports.map((r) => r.position.x + getNodeDim(r).w)
    const reportsMidpoint = (Math.min(...lefts) + Math.max(...rights)) / 2
    expect(lead.position.x + getNodeDim(lead).w / 2).toBeCloseTo(reportsMidpoint, 5)
  })

  it('leaves the global top-to-bottom flow unchanged', () => {
    const order = ['owner-owner-1', ROOT_DEPT_NODE_ID, 'dept-ops']
    expect(topToBottom(layoutOf(orgConfig(MIXED_ORG)), order)).toEqual(order)
  })

  it('still renders departments in the operator\'s order', () => {
    const order = ['dept-ops', 'dept-engineering']
    expect(leftToRight(layoutOf(orgConfig(MIXED_ORG)), order)).toEqual(order)
  })

  it('fills a wrapped rank in the operator\'s order, row by row', () => {
    const [, reports] = membersOf(layoutOf(orgConfig(MIXED_ORG)), 'engineering')
    const laidOut = rowsOf(reports).flat().map((n) => n.id)
    expect(laidOut).toEqual(agentIds(['bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'heidi']))
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

  it('overlaps no two cards anywhere in the chart', () => {
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
