import { describe, expect, it } from 'vitest'
import {
  DEFAULT_NODE_SEP,
  DEFAULT_NODE_WIDTH,
  getNodeDim,
} from '@/pages/org/layout-shared'
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
    // Only the head sits outside the staffed team, so the department box has
    // nothing but that team box, an unstaffed team and the head to wrap.
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
    const team = getNodeDim(nodeById(layoutOf(orgConfig(TEAMED_ORG)), CORE))
    expect(team.w).toBeGreaterThan(0)
    expect(team.h).toBeGreaterThan(0)
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
    expect(getNodeDim(unstaffed).w).toBeGreaterThan(0)
    expect(getNodeDim(unstaffed).h).toBeGreaterThan(0)
    expect(childrenOf(nodes, UNSTAFFED)).toHaveLength(0)
    expect(fitsInside(unstaffed, nodeById(nodes, 'dept-platform'))).toBe(true)
  })

  it('sizes a department around its team box and its head', () => {
    const nodes = layoutOf(orgConfig(TEAMED_ORG))
    const dept = getNodeDim(nodeById(nodes, 'dept-platform'))
    const team = getNodeDim(nodeById(nodes, PLATFORM))
    expect(dept.w).toBeGreaterThanOrEqual(team.w)
    expect(dept.h).toBeGreaterThanOrEqual(team.h)
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

  it('wraps a team\'s members into a block rather than one long line', () => {
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
    // Seven reports on one line would measure seven cards plus six gaps. Wrapped
    // into a block they occupy three columns, so the team box is far narrower.
    const oneLine = 7 * DEFAULT_NODE_WIDTH + 6 * DEFAULT_NODE_SEP
    expect(getNodeDim(team).w).toBeLessThan(oneLine)
    expect(new Set(reports.map((r) => r.position.x)).size).toBe(3)
    expect(new Set(reports.map((r) => r.position.y)).size).toBe(3)
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
    // Both survivors report to the same team lead, so they share a rank and a
    // frame and a swap between them is a real reorder. A single id sorts to
    // itself whatever the layout does, and the lead is no good either: it is
    // centred across its reports, so losing one moves it for a reason that is
    // not a reorder at all.
    const engineering = (teamMembers: string[]): DeptSpec => ({
      name: 'engineering',
      members: ['alice', 'bob', 'carol', 'dave', 'erin'],
      teams: [{ name: 'core', members: teamMembers }],
    })
    const survivors = agentIds(['carol', 'dave'])
    const staffed = [TEAMED_ORG[0]!, engineering(['bob', 'carol', 'dave', 'erin']), TEAMED_ORG[2]!]
    const before = leftToRight(layoutOf(orgConfig(staffed)), survivors)

    const nodes = layoutOf(orgConfig([
      TEAMED_ORG[0]!,
      engineering(['bob', 'carol', 'dave']),
      TEAMED_ORG[2]!,
    ]))
    expect(leftToRight(nodes, survivors)).toEqual(before)
    // erin left the team, so it now draws her beside her department head.
    expect(childrenOf(nodes, CORE).map((n) => n.id)).not.toContain('agent-erin')
    expect(childrenOf(nodes, 'dept-engineering').map((n) => n.id)).toContain('agent-erin')
  })

  it('measures a team box when choosing its department\'s direction', () => {
    // Four members is a rank the budget accommodates as plain cards, so a
    // member count alone reads this department as top-to-bottom. Each of these
    // members is a team box holding a lead over two reports, which is more
    // than twice a card wide, and the four side by side overrun the budget:
    // the department must measure them and flow left-to-right instead.
    const manyTeams: DeptSpec[] = [
      { name: 'executive', members: ['zoe'] },
      {
        name: 'engineering',
        members: [
          'alice',
          't1a', 't1b', 't1c',
          't2a', 't2b', 't2c',
          't3a', 't3b', 't3c',
          't4a', 't4b', 't4c',
        ],
        teams: [
          { name: 'one', members: ['t1a', 't1b', 't1c'] },
          { name: 'two', members: ['t2a', 't2b', 't2c'] },
          { name: 'three', members: ['t3a', 't3b', 't3c'] },
          { name: 'four', members: ['t4a', 't4b', 't4c'] },
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
    // The arrangement itself, not just the absence of overlap: four boxes on one
    // line would also pass the two assertions above, so without this the case
    // survives the very regression it is named for. Four wrap two and two.
    expect(new Set(teamBoxes.map((b) => b.position.x)).size).toBe(2)
    expect(new Set(teamBoxes.map((b) => b.position.y)).size).toBe(2)
  })
})
