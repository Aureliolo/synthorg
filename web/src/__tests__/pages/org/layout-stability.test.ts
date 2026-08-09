import { describe, expect, it } from 'vitest'
import type { DashboardAgentConfig } from '@/api/types/agents'
import type { CompanyConfig } from '@/api/types/org'
import {
  type DeptSpec,
  OWNER_NODE_ID,
  ROOT_DEPT_NODE_ID,
  agentIds,
  fitsInside,
  layoutOf,
  leftToRight,
  nodeById,
  orgConfig,
  overlaps,
  pairsOf,
  positionsOf,
} from '../../helpers/org-layout'
import { makeDepartmentHealth } from '../../helpers/factories'

/**
 * Re-emit the flat agent array round-robin across departments, keeping each
 * department's own member order intact. Only the interleaving changes, so the
 * operator's order is untouched and the layout must not move.
 */
function interleaveAgents(config: CompanyConfig): CompanyConfig {
  const byDept = new Map<string, DashboardAgentConfig[]>()
  for (const agent of config.agents) {
    const bucket = byDept.get(agent.department) ?? []
    bucket.push(agent)
    byDept.set(agent.department, bucket)
  }
  const buckets = [...byDept.values()]
  const deepest = Math.max(...buckets.map((b) => b.length))
  const woven: DashboardAgentConfig[] = []
  for (let i = 0; i < deepest; i++) {
    for (const bucket of buckets) {
      const agent = bucket[i]
      if (agent) woven.push(agent)
    }
  }
  return { ...config, agents: woven }
}

const SMALL_ORG: readonly DeptSpec[] = [
  { name: 'executive', members: ['zoe'] },
  { name: 'engineering', members: ['alice', 'bob', 'carol'] },
  { name: 'sales', members: ['dan', 'erin'] },
]

// ── Tests ───────────────────────────────────────────────────

describe('org chart layout ordering', () => {
  it('renders a department\'s agents in the order the config supplied', () => {
    const nodes = layoutOf(orgConfig(SMALL_ORG))
    const reports = agentIds(['bob', 'carol'])
    expect(leftToRight(nodes, reports)).toEqual(reports)
  })

  it('renders departments in the order the config supplied', () => {
    const nodes = layoutOf(orgConfig(SMALL_ORG))
    const nonRoot = ['dept-engineering', 'dept-sales']
    expect(leftToRight(nodes, nonRoot)).toEqual(nonRoot)
  })

  it('honours a reordered department list', () => {
    const reordered: DeptSpec[] = [
      SMALL_ORG[0]!,
      SMALL_ORG[2]!,
      SMALL_ORG[1]!,
    ]
    const nodes = layoutOf(orgConfig(reordered))
    expect(leftToRight(nodes, ['dept-sales', 'dept-engineering'])).toEqual([
      'dept-sales',
      'dept-engineering',
    ])
  })

  it('honours a reordered agent list within a department', () => {
    const reordered: DeptSpec[] = [
      SMALL_ORG[0]!,
      { name: 'engineering', members: ['alice', 'carol', 'bob'] },
      SMALL_ORG[2]!,
    ]
    const nodes = layoutOf(orgConfig(reordered))
    expect(leftToRight(nodes, agentIds(['carol', 'bob']))).toEqual(
      agentIds(['carol', 'bob']),
    )
  })

  it('is invariant to how the API interleaved the flat agent array', () => {
    const config = orgConfig(SMALL_ORG)
    expect(positionsOf(layoutOf(interleaveAgents(config)))).toEqual(
      positionsOf(layoutOf(config)),
    )
  })

  it('is invariant to live agent status and department health', () => {
    // The rendered chart memoises its placement on the org's structure alone,
    // which is only sound while a status or health frame cannot move a node.
    const config = orgConfig(SMALL_ORG)
    const live = layoutOf(config, {
      runtimeStatuses: { 'agent-bob': 'active', 'agent-carol': 'error' },
      departmentHealths: [makeDepartmentHealth('engineering')],
    })
    expect(positionsOf(live)).toEqual(positionsOf(layoutOf(config)))
  })

  it('is invariant to what was laid out before it', () => {
    const small = orgConfig(SMALL_ORG)
    const first = positionsOf(layoutOf(small))
    layoutOf(
      orgConfig([
        ...SMALL_ORG,
        { name: 'legal', members: ['finn', 'gina', 'hank', 'iris'] },
        { name: 'support', members: ['jack', 'kim'] },
      ]),
    )
    expect(positionsOf(layoutOf(small))).toEqual(first)
  })
})

describe('org chart layout under structural change', () => {
  it('adding an agent does not reorder the survivors', () => {
    const before = leftToRight(
      layoutOf(orgConfig(SMALL_ORG)),
      agentIds(['bob', 'carol']),
    )
    const grown: DeptSpec[] = [
      SMALL_ORG[0]!,
      { name: 'engineering', members: ['alice', 'bob', 'carol', 'dave'] },
      SMALL_ORG[2]!,
    ]
    const after = leftToRight(layoutOf(orgConfig(grown)), agentIds(['bob', 'carol']))
    expect(after).toEqual(before)
  })

  it('removing an agent does not reorder the survivors', () => {
    const before = leftToRight(
      layoutOf(orgConfig(SMALL_ORG)),
      agentIds(['bob', 'carol']),
    )
    const shrunk: DeptSpec[] = [
      SMALL_ORG[0]!,
      SMALL_ORG[1]!,
      { name: 'sales', members: ['dan'] },
    ]
    const after = leftToRight(layoutOf(orgConfig(shrunk)), agentIds(['bob', 'carol']))
    expect(after).toEqual(before)
  })

  it('adding a department does not reorder the surviving departments', () => {
    const survivors = ['dept-engineering', 'dept-sales']
    const before = leftToRight(layoutOf(orgConfig(SMALL_ORG)), survivors)
    const grown: DeptSpec[] = [
      ...SMALL_ORG,
      { name: 'legal', members: ['finn', 'gina'] },
    ]
    expect(leftToRight(layoutOf(orgConfig(grown)), survivors)).toEqual(before)
  })

  it('renders several executives in the operator\'s order', () => {
    const wideExec: DeptSpec[] = [
      { name: 'executive', members: ['zoe', 'cto', 'cfo', 'coo'] },
      SMALL_ORG[1]!,
    ]
    const execs = agentIds(['cto', 'cfo', 'coo'])
    expect(leftToRight(layoutOf(orgConfig(wideExec)), execs)).toEqual(execs)
  })

  it('keeps an unstaffed department in its configured slot', () => {
    const withEmpty: DeptSpec[] = [
      SMALL_ORG[0]!,
      SMALL_ORG[1]!,
      { name: 'legal', members: [] },
      SMALL_ORG[2]!,
    ]
    const nodes = layoutOf(orgConfig(withEmpty))
    const order = ['dept-engineering', 'dept-legal', 'dept-sales']
    expect(leftToRight(nodes, order)).toEqual(order)
  })

  it('removing a department does not reorder the surviving departments', () => {
    const wide: DeptSpec[] = [
      ...SMALL_ORG,
      { name: 'legal', members: ['finn', 'gina'] },
    ]
    const survivors = ['dept-engineering', 'dept-legal']
    const before = leftToRight(layoutOf(orgConfig(wide)), survivors)
    const shrunk = wide.filter((d) => d.name !== 'sales')
    expect(leftToRight(layoutOf(orgConfig(shrunk)), survivors)).toEqual(before)
  })
})

describe('org chart layout spine anchor', () => {
  const SPINE = [OWNER_NODE_ID, ROOT_DEPT_NODE_ID]

  function spineOf(config: CompanyConfig): Record<string, { x: number; y: number }> {
    const nodes = layoutOf(config)
    const spine = nodes.filter((n) => SPINE.includes(n.id))
    expect(spine).toHaveLength(SPINE.length)
    return positionsOf(spine)
  }

  it('centres the root department box on the origin and starts the chart at y zero', () => {
    const nodes = layoutOf(orgConfig(SMALL_ORG))
    const root = nodes.find((n) => n.id === ROOT_DEPT_NODE_ID)!
    expect(root.position.x + (root.width as number) / 2).toBeCloseTo(0, 5)
    expect(Math.min(...nodes.filter((n) => !n.parentId).map((n) => n.position.y))).toBeCloseTo(0, 5)
  })

  it('holds the spine still when a department is added', () => {
    const before = spineOf(orgConfig(SMALL_ORG))
    const grown: DeptSpec[] = [
      ...SMALL_ORG,
      { name: 'legal', members: ['finn', 'gina', 'hank'] },
    ]
    expect(spineOf(orgConfig(grown))).toEqual(before)
  })

  it('holds the spine still when a department is removed', () => {
    const before = spineOf(orgConfig(SMALL_ORG))
    const shrunk = SMALL_ORG.filter((d) => d.name !== 'sales')
    expect(spineOf(orgConfig(shrunk))).toEqual(before)
  })

  it('holds the spine still when an agent is added to a department', () => {
    const before = spineOf(orgConfig(SMALL_ORG))
    const grown: DeptSpec[] = [
      SMALL_ORG[0]!,
      { name: 'engineering', members: ['alice', 'bob', 'carol', 'dave'] },
      SMALL_ORG[2]!,
    ]
    expect(spineOf(orgConfig(grown))).toEqual(before)
  })

  it('holds the spine still when a department is wide enough to flow sideways', () => {
    const withWide: DeptSpec[] = [
      ...SMALL_ORG,
      {
        name: 'engineering-wide',
        members: ['w0', 'w1', 'w2', 'w3', 'w4', 'w5', 'w6', 'w7'],
      },
    ]
    // The sideways department changes its card's aspect ratio, which feeds the
    // vertical-gap and anchor passes; the spine must survive that.
    expect(spineOf(orgConfig(withWide))).toEqual(spineOf(orgConfig(SMALL_ORG)))
  })

  it('holds the spine still when an agent is removed', () => {
    const before = spineOf(orgConfig(SMALL_ORG))
    const shrunk: DeptSpec[] = [
      SMALL_ORG[0]!,
      { name: 'engineering', members: ['alice', 'bob'] },
      SMALL_ORG[2]!,
    ]
    expect(spineOf(orgConfig(shrunk))).toEqual(before)
  })

  it('holds the spine still with the card chrome toggles on', () => {
    const config = orgConfig(SMALL_ORG)
    const chrome = {
      layout: { showBudgetBar: true, showStatusDots: true, showAddAgentButton: true },
    }
    const before = positionsOf(
      layoutOf(config, chrome).filter((n) => SPINE.includes(n.id)),
    )
    const grown = orgConfig([...SMALL_ORG, { name: 'legal', members: ['finn', 'gina'] }])
    expect(
      positionsOf(layoutOf(grown, chrome).filter((n) => SPINE.includes(n.id))),
    ).toEqual(before)
  })

  it('centres the median department under the root for an odd count', () => {
    const odd = orgConfig([
      SMALL_ORG[0]!,
      SMALL_ORG[1]!,
      SMALL_ORG[2]!,
      { name: 'legal', members: ['finn', 'gina'] },
    ])
    const nodes = layoutOf(odd)
    const root = nodeById(nodes, ROOT_DEPT_NODE_ID)
    const nonRoot = nodes.filter(
      (n) => n.type === 'department' && n.id !== ROOT_DEPT_NODE_ID,
    )
    const centres = nonRoot
      .map((n) => n.position.x + (n.width as number) / 2)
      .sort((a, b) => a - b)
    const rootCentre = root.position.x + (root.width as number) / 2
    expect(centres[(centres.length - 1) / 2]).toBeCloseTo(rootCentre, 5)
  })
})

describe('org chart layout with several owners', () => {
  const TWO_OWNERS = [
    { id: 'owner-1', displayName: 'First Owner' },
    { id: 'owner-2', displayName: 'Second Owner' },
  ]

  it('keeps the owners apart and centres the row over the root', () => {
    const nodes = layoutOf(orgConfig(SMALL_ORG), { owners: TWO_OWNERS })
    const owners = ['owner-owner-1', 'owner-owner-2'].map((id) => nodeById(nodes, id))
    expect(owners[0]!.position.x).not.toBeCloseTo(owners[1]!.position.x, 1)
    for (const [a, b] of pairsOf(owners)) expect(overlaps(a, b)).toBe(false)

    const root = nodeById(nodes, ROOT_DEPT_NODE_ID)
    const rowCentre =
      owners.reduce((sum, o) => sum + o.position.x + (o.width as number) / 2, 0) / owners.length
    expect(rowCentre).toBeCloseTo(root.position.x + (root.width as number) / 2, 5)
  })

  it('holds both owners still when a department is added', () => {
    const before = positionsOf(
      layoutOf(orgConfig(SMALL_ORG), { owners: TWO_OWNERS }).filter((n) => n.type === 'owner'),
    )
    const grown = orgConfig([...SMALL_ORG, { name: 'legal', members: ['finn', 'gina'] }])
    expect(
      positionsOf(layoutOf(grown, { owners: TWO_OWNERS }).filter((n) => n.type === 'owner')),
    ).toEqual(before)
  })
})

describe('org chart layout with department admins', () => {
  const ADMINS = [{ id: 'ada', displayName: 'Ada Admin', department: 'engineering' }]

  it('places a department admin inside its department without overlaps', () => {
    const nodes = layoutOf(orgConfig(SMALL_ORG), { deptAdmins: ADMINS })
    const dept = nodeById(nodes, 'dept-engineering')
    const admin = nodeById(nodes, 'dept-admin-ada')
    expect(admin.parentId).toBe('dept-engineering')
    expect(fitsInside(admin, dept)).toBe(true)
    for (const [a, b] of pairsOf(nodes.filter((n) => n.parentId === dept.id))) {
      expect(overlaps(a, b)).toBe(false)
    }
  })

  it('does not reorder the department\'s agents around the admin', () => {
    const reports = agentIds(['bob', 'carol'])
    expect(leftToRight(layoutOf(orgConfig(SMALL_ORG), { deptAdmins: ADMINS }), reports))
      .toEqual(reports)
  })
})
