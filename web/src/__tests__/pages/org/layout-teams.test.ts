import { describe, expect, it } from 'vitest'
import type { Node } from '@xyflow/react'
import { getNodeDim } from '@/pages/org/layout-shared'
import {
  type DeptSpec,
  agentIds,
  layoutOf,
  leftToRight,
  nodeById,
  orgConfig,
} from '../../helpers/org-layout'

const CORE = 'team-engineering-core'
const PLATFORM = 'team-platform-tools'

const TEAMED_ORG: readonly DeptSpec[] = [
  { name: 'executive', members: ['zoe'] },
  {
    name: 'engineering',
    members: ['alice', 'bob', 'carol', 'dave', 'erin'],
    teams: [{ name: 'core', members: ['bob', 'carol', 'dave'] }],
  },
  {
    // Every member belongs to the one team, so the department box has nothing
    // but the team box and its own head to wrap.
    name: 'platform',
    members: ['pia', 'paul', 'pearl'],
    teams: [{ name: 'tools', members: ['paul', 'pearl'] }],
  },
]

function childrenOf(nodes: readonly Node[], parentId: string): Node[] {
  return nodes.filter((n) => n.parentId === parentId)
}

function fitsInside(child: Node, parent: Node): boolean {
  const { w, h } = getNodeDim(child)
  return (
    child.position.x >= 0
    && child.position.y >= 0
    && child.position.x + w <= (parent.width as number)
    && child.position.y + h <= (parent.height as number)
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

describe('team boxes', () => {
  it('sizes a team box around its members', () => {
    const nodes = layoutOf(orgConfig(TEAMED_ORG))
    const team = nodeById(nodes, CORE)
    expect(team.width as number).toBeGreaterThan(0)
    expect(team.height as number).toBeGreaterThan(0)
  })

  it('positions a team member relative to its team box', () => {
    const nodes = layoutOf(orgConfig(TEAMED_ORG))
    const team = nodeById(nodes, CORE)
    const members = childrenOf(nodes, CORE)
    expect(members).toHaveLength(3)
    for (const member of members) {
      expect(fitsInside(member, team)).toBe(true)
    }
  })

  it('keeps a team box inside its department box', () => {
    const nodes = layoutOf(orgConfig(TEAMED_ORG))
    expect(fitsInside(nodeById(nodes, CORE), nodeById(nodes, 'dept-engineering'))).toBe(true)
    expect(fitsInside(nodeById(nodes, PLATFORM), nodeById(nodes, 'dept-platform'))).toBe(true)
  })

  it('sizes a department around its team box when every member is in the team', () => {
    const nodes = layoutOf(orgConfig(TEAMED_ORG))
    const dept = nodeById(nodes, 'dept-platform')
    const team = nodeById(nodes, PLATFORM)
    expect(dept.width as number).toBeGreaterThanOrEqual(team.width as number)
    expect(dept.height as number).toBeGreaterThanOrEqual(team.height as number)
  })

  it('renders team members in the operator\'s order', () => {
    const nodes = layoutOf(orgConfig(TEAMED_ORG))
    const reports = agentIds(['carol', 'dave'])
    expect(leftToRight(nodes, reports)).toEqual(reports)
  })

  it('keeps an unassigned member out of the team box', () => {
    const nodes = layoutOf(orgConfig(TEAMED_ORG))
    expect(childrenOf(nodes, CORE).map((n) => n.id)).not.toContain('agent-erin')
    expect(childrenOf(nodes, 'dept-engineering').map((n) => n.id)).toContain('agent-erin')
  })

  it('overlaps nothing inside a department that mixes a team with loose members', () => {
    const nodes = layoutOf(orgConfig(TEAMED_ORG))
    const siblings = childrenOf(nodes, 'dept-engineering')
    for (let i = 0; i < siblings.length; i++) {
      for (let j = i + 1; j < siblings.length; j++) {
        expect(overlaps(siblings[i]!, siblings[j]!)).toBe(false)
      }
    }
  })
})
