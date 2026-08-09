import { describe, expect, it } from 'vitest'
import { DEPT_HORIZONTAL_WIDTH_BUDGET } from '@/pages/org/layout-shared'
import {
  type DeptSpec,
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

const CORE = 'team-engineering-core'
const PLATFORM = 'team-platform-tools'
const UNSTAFFED = 'team-platform-future'

const TEAMED_ORG: readonly DeptSpec[] = [
  { name: 'executive', members: ['zoe'] },
  {
    name: 'engineering',
    members: ['alice', 'bob', 'carol', 'dave', 'erin'],
    teams: [{ name: 'core', members: ['bob', 'carol', 'dave'] }],
  },
  {
    // Every member belongs to the one staffed team, so the department box has
    // nothing but that box, an unstaffed team and its own head to wrap.
    name: 'platform',
    members: ['pia', 'paul', 'pearl'],
    teams: [
      { name: 'tools', members: ['paul', 'pearl'] },
      { name: 'future', members: [] },
    ],
  },
]

describe('team boxes', () => {
  it('sizes a team box around its members', () => {
    const team = nodeById(layoutOf(orgConfig(TEAMED_ORG)), CORE)
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

  it('gives an unstaffed team a card of its own inside the department', () => {
    const nodes = layoutOf(orgConfig(TEAMED_ORG))
    const unstaffed = nodeById(nodes, UNSTAFFED)
    expect(unstaffed.width as number).toBeGreaterThan(0)
    expect(unstaffed.height as number).toBeGreaterThan(0)
    expect(childrenOf(nodes, UNSTAFFED)).toHaveLength(0)
    expect(fitsInside(unstaffed, nodeById(nodes, 'dept-platform'))).toBe(true)
  })

  it('sizes a department around its team box when every member is in a team', () => {
    const nodes = layoutOf(orgConfig(TEAMED_ORG))
    const dept = nodeById(nodes, 'dept-platform')
    const team = nodeById(nodes, PLATFORM)
    expect(dept.width as number).toBeGreaterThanOrEqual(team.width as number)
    expect(dept.height as number).toBeGreaterThanOrEqual(team.height as number)
  })

  it('renders team members in the operator\'s order', () => {
    const reports = agentIds(['carol', 'dave'])
    expect(leftToRight(layoutOf(orgConfig(TEAMED_ORG)), reports)).toEqual(reports)
  })

  it('keeps an unassigned member out of the team box', () => {
    const nodes = layoutOf(orgConfig(TEAMED_ORG))
    expect(childrenOf(nodes, CORE).map((n) => n.id)).not.toContain('agent-erin')
    expect(childrenOf(nodes, 'dept-engineering').map((n) => n.id)).toContain('agent-erin')
  })

  it('overlaps nothing inside a department that mixes a team with loose members', () => {
    const siblings = childrenOf(layoutOf(orgConfig(TEAMED_ORG)), 'dept-engineering')
    for (const [a, b] of pairsOf(siblings)) {
      expect(overlaps(a, b)).toBe(false)
    }
  })

  it('lays a team out in its own direction once it overruns the width budget', () => {
    const wide: DeptSpec[] = [
      { name: 'executive', members: ['zoe'] },
      {
        name: 'engineering',
        members: ['alice', 'lead', 'm1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7'],
        teams: [{ name: 'core', members: ['lead', 'm1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7'] }],
      },
    ]
    const nodes = layoutOf(orgConfig(wide))
    const team = nodeById(nodes, CORE)
    const reports = childrenOf(nodes, CORE).filter((n) => n.id !== 'agent-lead')
    // Seven reports laid out in a row would blow far past the budget; in a
    // column the team box stays inside it.
    expect(team.width as number).toBeLessThanOrEqual(DEPT_HORIZONTAL_WIDTH_BUDGET)
    expect(new Set(reports.map((r) => r.position.x)).size).toBe(1)
    expect(topToBottom(nodes, agentIds(['m1', 'm7']))).toEqual(agentIds(['m1', 'm7']))
  })

  it('lays out a team whose declared lead is not one of its members', () => {
    const orphanLead: DeptSpec[] = [
      { name: 'executive', members: ['zoe'] },
      {
        name: 'engineering',
        members: ['alice', 'bob', 'carol'],
        teams: [{ name: 'core', members: ['bob', 'carol'], lead: 'nobody' }],
      },
    ]
    const nodes = layoutOf(orgConfig(orphanLead))
    const members = childrenOf(nodes, CORE)
    expect(members).toHaveLength(2)
    for (const member of members) {
      expect(fitsInside(member, nodeById(nodes, CORE))).toBe(true)
    }
    for (const [a, b] of pairsOf(members)) {
      expect(overlaps(a, b)).toBe(false)
    }
  })

  it('does not reorder the survivors when a team is added', () => {
    const survivors = agentIds(['carol', 'dave'])
    const before = leftToRight(layoutOf(orgConfig(TEAMED_ORG)), survivors)
    const grown: DeptSpec[] = [
      TEAMED_ORG[0]!,
      {
        name: 'engineering',
        members: ['alice', 'bob', 'carol', 'dave', 'erin'],
        teams: [
          { name: 'core', members: ['bob', 'carol', 'dave'] },
          { name: 'later', members: ['erin'] },
        ],
      },
      TEAMED_ORG[2]!,
    ]
    expect(leftToRight(layoutOf(orgConfig(grown)), survivors)).toEqual(before)
  })

  it('does not reorder the survivors when a member leaves a team', () => {
    const survivors = agentIds(['carol'])
    const before = leftToRight(layoutOf(orgConfig(TEAMED_ORG)), survivors)
    const shrunk: DeptSpec[] = [
      TEAMED_ORG[0]!,
      {
        name: 'engineering',
        members: ['alice', 'bob', 'carol', 'dave', 'erin'],
        teams: [{ name: 'core', members: ['bob', 'carol'] }],
      },
      TEAMED_ORG[2]!,
    ]
    const nodes = layoutOf(orgConfig(shrunk))
    expect(leftToRight(nodes, survivors)).toEqual(before)
    // dave left the team, so it now draws him beside his department head.
    expect(childrenOf(nodes, CORE).map((n) => n.id)).not.toContain('agent-dave')
    expect(childrenOf(nodes, 'dept-engineering').map((n) => n.id)).toContain('agent-dave')
  })

  it('counts a team box when choosing its department\'s direction', () => {
    // Four teams side by side would overrun the budget, so the department
    // itself must flow left-to-right and stack them instead.
    const manyTeams: DeptSpec[] = [
      { name: 'executive', members: ['zoe'] },
      {
        name: 'engineering',
        members: ['alice', 't1a', 't1b', 't2a', 't2b', 't3a', 't3b', 't4a', 't4b'],
        teams: [
          { name: 'one', members: ['t1a', 't1b'] },
          { name: 'two', members: ['t2a', 't2b'] },
          { name: 'three', members: ['t3a', 't3b'] },
          { name: 'four', members: ['t4a', 't4b'] },
        ],
      },
    ]
    const nodes = layoutOf(orgConfig(manyTeams))
    const teamBoxes = ['one', 'two', 'three', 'four'].map((n) =>
      nodeById(nodes, `team-engineering-${n}`),
    )
    for (const [a, b] of pairsOf(teamBoxes)) {
      expect(overlaps(a, b)).toBe(false)
    }
    for (const box of teamBoxes) {
      expect(fitsInside(box, nodeById(nodes, 'dept-engineering'))).toBe(true)
    }
  })
})
