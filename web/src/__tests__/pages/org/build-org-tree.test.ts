import { describe, expect, it } from 'vitest'
import { buildOrgTree } from '@/pages/org/build-org-tree'
import {
  findCeo,
  findHighestSeniority,
  humanizeDepartmentName,
} from '@/pages/org/build-org-tree-types'
import type {
  AgentNodeData,
  DepartmentGroupData,
  OwnerInfo,
} from '@/pages/org/build-org-tree'
import type { AgentConfig } from '@/api/types/agents'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { DepartmentName } from '@/api/types/enums'
import type { CompanyConfig } from '@/api/types/org'
import type { AgentRuntimeStatus } from '@/lib/utils'

// Positional adapter over the args-object `buildOrgTree` signature so
// the table-driven test cases below stay terse.
function buildTree(
  config: CompanyConfig,
  runtimeStatuses: Record<string, AgentRuntimeStatus> = {},
  departmentHealths: readonly DepartmentHealth[] = [],
  owners?: readonly OwnerInfo[],
): ReturnType<typeof buildOrgTree> {
  return buildOrgTree({ config, runtimeStatuses, departmentHealths, owners })
}

// ── Test helpers ────────────────────────────────────────────

function makeAgent(overrides: Partial<AgentConfig> & { name: string; id?: string }): AgentConfig {
  return {
    role: 'Developer',
    department: 'engineering',
    level: 'mid',
    status: 'active',
    personality: {
      traits: [],
      communication_style: 'direct',
      risk_tolerance: 'medium',
      creativity: 'medium',
      description: '',
      openness: 0.5,
      conscientiousness: 0.5,
      extraversion: 0.5,
      agreeableness: 0.5,
      stress_response: 0.5,
      decision_making: 'analytical',
      collaboration: 'team',
      verbosity: 'balanced',
      conflict_approach: 'collaborate',
    },
    model: { provider: 'test', model_id: 'test-001', temperature: 0.7, max_tokens: 4096, fallback_model: null },
    memory: { type: 'session', retention_days: null },
    tools: { access_level: 'standard', allowed: [], denied: [] },
    authority: {},
    autonomy_level: null,
    strategic_output_mode: null,
    personality_preset: null,
    tier: null,
    model_requirement: null,
    hiring_date: '2026-01-01',
    ...overrides,
  }
}

function makeDept(
  name: string,
  displayName: string,
  teams: { name: string; lead: string; members: readonly string[] }[] = [],
): CompanyConfig['departments'][number] {
  return {
    name,
    display_name: displayName,
    autonomy_level: null,
    budget_percent: 0,
    ceremony_policy: null,
    head: null,
    head_id: null,
    policies: {
      approval_chains: [],
      review_requirements: {
        min_reviewers: 0,
        required_reviewer_roles: [],
        self_review_allowed: true,
      },
    },
    reporting_lines: [],
    teams,
  }
}

function makeConfig(agents: AgentConfig[], departments?: CompanyConfig['departments']): CompanyConfig {
  const deptNames = [...new Set(agents.map((a) => a.department))]
  return {
    company_name: 'Test Corp',
    agents,
    departments: departments ?? deptNames.map((name) => ({
      name,
      display_name: name.charAt(0).toUpperCase() + name.slice(1),
      autonomy_level: null,
      budget_percent: 0,
      ceremony_policy: null,
      head: null,
      head_id: null,
      policies: {
        approval_chains: [],
        review_requirements: {
          min_reviewers: 0,
          required_reviewer_roles: [],
          self_review_allowed: true,
        },
      },
      reporting_lines: [],
      teams: [],
    })),
  }
}

function makeHealth(name: DepartmentName, utilizationPercent: number): DepartmentHealth {
  return {
    department_name: name,
    agent_count: 3,
    active_agent_count: 2,
    currency: 'EUR',
    avg_performance_score: 7.5,
    department_cost_7d: 12.5,
    cost_trend: [],
    collaboration_score: 6.0,
    utilization_percent: utilizationPercent,
  }
}

function makeOwners(): OwnerInfo[] {
  return [{ id: 'owner-1', displayName: 'Test Owner' }]
}

/**
 * Helper for tests that care about the structural restructure
 * (introduced alongside the Owner + Root-Department rendering
 * model): CEO agents live INSIDE the root dept box with
 * `data.isCompanyCeo === true`, not as standalone `type: 'ceo'`
 * nodes.  There is no `type: 'ceo'` anymore.
 */
function findCompanyCeo(nodes: ReturnType<typeof buildOrgTree>['nodes']) {
  return nodes.find(
    (n) => n.type === 'agent' && (n.data as AgentNodeData).isCompanyCeo === true,
  )
}

// ── Tests ───────────────────────────────────────────────────

describe('buildOrgTree', () => {
  it('returns empty nodes and edges for empty config', () => {
    const config = makeConfig([])
    const result = buildTree(config, {}, [])
    expect(result.nodes).toEqual([])
    expect(result.edges).toEqual([])
  })

  it('marks the c_suite agent in the executive department as the company CEO', () => {
    const agents = [
      makeAgent({ id: 'ceo-1', name: 'Alice', role: 'CEO', department: 'executive', level: 'c_suite' }),
      makeAgent({ id: 'dev-1', name: 'Bob', department: 'engineering', level: 'senior' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const ceo = findCompanyCeo(result.nodes)
    expect(ceo).toBeDefined()
    expect((ceo!.data as AgentNodeData).name).toBe('Alice')
    expect(ceo!.parentId).toBe('dept-executive')
  })

  it('falls back to a c_suite agent in a non-executive department as the company CEO', () => {
    const agents = [
      makeAgent({ id: 'cto', name: 'CTO', department: 'engineering', level: 'c_suite' }),
      makeAgent({ id: 'dev', name: 'Dev', department: 'engineering', level: 'mid' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const ceo = findCompanyCeo(result.nodes)
    expect(ceo).toBeDefined()
    expect((ceo!.data as AgentNodeData).name).toBe('CTO')
    expect(ceo!.parentId).toBe('dept-engineering')
  })

  it('falls back to the highest-seniority agent when no c_suite exists', () => {
    const agents = [
      makeAgent({ id: 'lead-1', name: 'Carol', department: 'engineering', level: 'lead' }),
      makeAgent({ id: 'jr-1', name: 'Dave', department: 'engineering', level: 'junior' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const ceo = findCompanyCeo(result.nodes)
    expect(ceo).toBeDefined()
    expect((ceo!.data as AgentNodeData).name).toBe('Carol')
  })

  it('marks the CEO\'s home department as the root department', () => {
    const agents = [
      makeAgent({ id: 'ceo', name: 'CEO', department: 'executive', level: 'c_suite' }),
      makeAgent({ id: 'cto', name: 'CTO', department: 'engineering', level: 'c_suite' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const execDept = result.nodes.find((n) => n.id === 'dept-executive')
    expect(execDept).toBeDefined()
    expect((execDept!.data as DepartmentGroupData).isRootDepartment).toBe(true)

    const engDept = result.nodes.find((n) => n.id === 'dept-engineering')
    expect(engDept).toBeDefined()
    // Engineering is NOT the root even though its CTO is c_suite,
    // because the executive-dept c_suite takes priority as CEO.
    expect((engDept!.data as DepartmentGroupData).isRootDepartment).toBe(false)
  })

  it('groups agents by department via parentId', () => {
    const agents = [
      makeAgent({ id: 'a1', name: 'A1', department: 'engineering', level: 'lead' }),
      makeAgent({ id: 'a2', name: 'A2', department: 'engineering', level: 'mid' }),
      makeAgent({ id: 'a3', name: 'A3', department: 'product', level: 'lead' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const deptNodes = result.nodes.filter((n) => n.type === 'department')
    expect(deptNodes).toHaveLength(2)

    const engAgents = result.nodes.filter((n) => n.parentId === 'dept-engineering')
    expect(engAgents).toHaveLength(2)

    const prodAgents = result.nodes.filter((n) => n.parentId === 'dept-product')
    expect(prodAgents).toHaveLength(1)
  })

  it('creates edges from the root department box to each other department box', () => {
    const agents = [
      makeAgent({ id: 'ceo', name: 'CEO', department: 'executive', level: 'c_suite' }),
      makeAgent({ id: 'cto', name: 'CTO', department: 'engineering', level: 'c_suite' }),
      makeAgent({ id: 'cpo', name: 'CPO', department: 'product', level: 'c_suite' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    // Visible edges from the root dept box (dept-executive) to each
    // other dept box.  Non-visible (hidden) layout edges live
    // alongside but are filtered out here.
    const rootDeptOutEdges = result.edges.filter(
      (e) => e.source === 'dept-executive' && !e.hidden,
    )
    expect(rootDeptOutEdges.map((e) => e.target).sort()).toEqual(['dept-engineering', 'dept-product'])

    // Hidden layout edges from the CEO agent (inside exec) to each
    // other dept's head agent -- dagre needs these to rank the
    // other depts below the executive subtree.
    const hiddenCeoEdges = result.edges.filter((e) => e.source === 'ceo' && e.hidden === true)
    expect(hiddenCeoEdges.map((e) => e.target).sort()).toEqual(['cpo', 'cto'])
    // All hidden cross-dept edges carry a `crossDeptKind` tag so
    // layout.ts can compute dynamic minlen per edge kind.
    for (const edge of hiddenCeoEdges) {
      const data = edge.data as { crossDeptKind?: string } | undefined
      expect(data?.crossDeptKind).toBe('ceo-to-child')
    }
  })

  it('creates owner nodes and wires them to the root department', () => {
    const agents = [
      makeAgent({ id: 'ceo', name: 'CEO', department: 'executive', level: 'c_suite' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [], makeOwners())

    const ownerNode = result.nodes.find((n) => n.type === 'owner')
    expect(ownerNode).toBeDefined()
    expect(ownerNode!.id).toBe('owner-owner-1')

    const visibleOwnerEdges = result.edges.filter((e) => e.source === 'owner-owner-1' && !e.hidden)
    expect(visibleOwnerEdges.map((e) => e.target)).toEqual(['dept-executive'])

    const hiddenOwnerEdges = result.edges.filter((e) => e.source === 'owner-owner-1' && e.hidden === true)
    expect(hiddenOwnerEdges.map((e) => e.target)).toEqual(['ceo'])
  })

  it('breaks ties deterministically for same-level peers (first in array wins)', () => {
    const agents = [
      makeAgent({ id: 'ceo', name: 'CEO', department: 'executive', level: 'c_suite' }),
      makeAgent({ id: 'vp-a', name: 'VP Alpha', department: 'engineering', level: 'vp' }),
      makeAgent({ id: 'vp-b', name: 'VP Beta', department: 'engineering', level: 'vp' }),
      makeAgent({ id: 'vp-c', name: 'VP Gamma', department: 'engineering', level: 'vp' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    // Visible edge: root dept box (executive) → engineering box
    const rootOut = result.edges.filter((e) => e.source === 'dept-executive' && !e.hidden)
    expect(rootOut.map((e) => e.target)).toEqual(['dept-engineering'])

    // Hidden layout edge: CEO → vp-a (engineering's dept head,
    // first VP in array order)
    const hiddenCeoEdges = result.edges.filter((e) => e.source === 'ceo' && e.hidden === true)
    expect(hiddenCeoEdges.map((e) => e.target)).toEqual(['vp-a'])

    // Inside engineering: vp-a is dept head; vp-b and vp-c report to vp-a
    const headEdges = result.edges.filter((e) => e.source === 'vp-a')
    expect(headEdges.map((e) => e.target).sort()).toEqual(['vp-b', 'vp-c'])
  })

  it('creates internal head→member edges within a department', () => {
    const agents = [
      makeAgent({ id: 'lead', name: 'Lead', department: 'engineering', level: 'lead' }),
      makeAgent({ id: 'dev1', name: 'Dev1', department: 'engineering', level: 'mid' }),
      makeAgent({ id: 'dev2', name: 'Dev2', department: 'engineering', level: 'junior' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    // `lead` is the CEO (highest seniority) and also the head of
    // engineering.  It emits edges to the other two members.
    const leadEdges = result.edges.filter((e) => e.source === 'lead' && !e.hidden)
    expect(leadEdges.map((e) => e.target).sort()).toEqual(['dev1', 'dev2'])
  })

  it('excludes terminated agents from the chart', () => {
    const agents = [
      makeAgent({ id: 'active-1', name: 'Active', department: 'engineering', level: 'lead', status: 'active' }),
      makeAgent({ id: 'fired-1', name: 'Fired', department: 'engineering', level: 'mid', status: 'terminated' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const agentNodes = result.nodes.filter((n) => n.type === 'agent')
    expect(agentNodes).toHaveLength(1)
    expect((agentNodes[0]!.data as AgentNodeData).name).toBe('Active')
  })

  it('creates department group nodes with health data', () => {
    const agents = [
      makeAgent({ id: 'a1', name: 'Dev', department: 'engineering', level: 'mid' }),
    ]
    const healthData = [makeHealth('engineering', 85)]
    const result = buildTree(makeConfig(agents), {}, healthData)

    const deptNode = result.nodes.find((n) => n.type === 'department')
    expect(deptNode).toBeDefined()
    const data = deptNode!.data as DepartmentGroupData
    expect(data.cost7d).toBe(12.5)
    expect(data.currency).toBe('EUR')
  })

  it('uses runtime status from the status map', () => {
    const agents = [
      makeAgent({ id: 'a1', name: 'Dev', department: 'engineering', level: 'mid', status: 'active' }),
    ]
    const result = buildTree(makeConfig(agents), { a1: 'error' }, [])

    const agentNode = result.nodes.find((n) => n.id === 'a1')
    expect((agentNode!.data as AgentNodeData).runtimeStatus).toBe('error')
  })

  it('uses team structure to derive reporting hierarchy', () => {
    const agents = [
      makeAgent({ id: 'lead', name: 'Lead', department: 'engineering', level: 'lead' }),
      makeAgent({ id: 'sr', name: 'Senior', department: 'engineering', level: 'senior' }),
      makeAgent({ id: 'jr', name: 'Junior', department: 'engineering', level: 'junior' }),
    ]
    const config = makeConfig(agents, [
      makeDept('engineering', 'Engineering', [
        { name: 'backend', lead: 'Lead', members: ['Lead', 'Senior', 'Junior'] },
      ]),
    ])
    const result = buildTree(config, {}, [])

    const leadEdges = result.edges.filter((e) => e.source === 'lead' && !e.hidden)
    expect(leadEdges.map((e) => e.target).sort()).toEqual(['jr', 'sr'])
    const edgeIds = result.edges.map((e) => e.id)
    expect(new Set(edgeIds).size).toBe(edgeIds.length)
  })

  it('renders empty departments with the isEmpty flag set', () => {
    const agents = [
      makeAgent({ id: 'a1', name: 'Dev', department: 'engineering', level: 'mid' }),
    ]
    const config: CompanyConfig = {
      company_name: 'Test',
      agents,
      departments: [
        makeDept('engineering', 'Engineering'),
        makeDept('product', 'Product'),
      ],
    }
    const result = buildTree(config, {}, [])

    const deptNodes = result.nodes.filter((n) => n.type === 'department')
    expect(deptNodes).toHaveLength(2)

    const productDept = deptNodes.find(
      (n) => (n.data as DepartmentGroupData).departmentName === 'product',
    )
    expect(productDept).toBeDefined()
    const productData = productDept!.data as DepartmentGroupData
    expect(productData.agentCount).toBe(0)
    expect(productData.isEmpty).toBe(true)
  })

  it('assigns correct node types for a populated org', () => {
    const agents = [
      makeAgent({ id: 'ceo', name: 'CEO', department: 'executive', level: 'c_suite' }),
      makeAgent({ id: 'dev', name: 'Dev', department: 'engineering', level: 'mid' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [], makeOwners())

    const types = result.nodes.map((n) => n.type).sort()
    // owner + 2 dept boxes + 2 agents (CEO + dev) = 5 nodes, no 'ceo' type
    expect(types).toEqual(['agent', 'agent', 'department', 'department', 'owner'])
  })

  it('returns null cost / currency when no health data provided', () => {
    const agents = [
      makeAgent({ id: 'a1', name: 'Dev', department: 'engineering', level: 'mid' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const deptNode = result.nodes.find((n) => n.type === 'department')
    const data = deptNode!.data as DepartmentGroupData
    expect(data.cost7d).toBeNull()
    expect(data.currency).toBeNull()
    // utilizationPercent is runtime-status-based (idle count / total) and
    // no longer derived from health data, so it has a value even with an
    // empty runtimeStatuses list (every agent falls back to its HR status).
    expect(data.utilizationPercent).not.toBeNull()
  })

  it('all edges have type "hierarchy"', () => {
    const agents = [
      makeAgent({ id: 'ceo', name: 'CEO', department: 'executive', level: 'c_suite' }),
      makeAgent({ id: 'cto', name: 'CTO', department: 'engineering', level: 'c_suite' }),
      makeAgent({ id: 'dev', name: 'Dev', department: 'engineering', level: 'mid' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    for (const edge of result.edges) {
      expect(edge.type).toBe('hierarchy')
    }
  })

  it('uses agent.name as node id when agent.id is undefined', () => {
    const agents = [
      makeAgent({ id: 'lead-1', name: 'Lead', department: 'engineering', level: 'lead' }),
      makeAgent({ id: undefined, name: 'NoIdAgent', department: 'engineering', level: 'mid' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const agentNode = result.nodes.find((n) => (n.data as AgentNodeData).name === 'NoIdAgent')
    expect(agentNode).toBeDefined()
    expect(agentNode!.id).toBe('NoIdAgent')
    expect((agentNode!.data as AgentNodeData).agentId).toBe('NoIdAgent')
  })

  it('treats agent without status as active (not filtered out)', () => {
    const agents = [
      makeAgent({ id: 'a0', name: 'Lead', department: 'engineering', level: 'lead' }),
      makeAgent({ id: 'a1', name: 'ActiveDefault', department: 'engineering', level: 'mid', status: undefined }),
      makeAgent({ id: 'a2', name: 'Terminated', department: 'engineering', level: 'mid', status: 'terminated' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const agentNames = result.nodes
      .filter((n) => n.type === 'agent')
      .map((n) => (n.data as AgentNodeData).name)
    expect(agentNames).toContain('ActiveDefault')
    expect(agentNames).not.toContain('Terminated')
  })
})

// ── Team group nodes ────────────────────────────────────────

describe('team group nodes', () => {
  it('emits team group nodes when department has teams', () => {
    const agents = [
      makeAgent({ id: 'a1', name: 'Alice', department: 'engineering', level: 'lead' }),
      makeAgent({ id: 'a2', name: 'Bob', department: 'engineering', level: 'mid' }),
    ]
    const depts = [
      makeDept('engineering', 'Engineering', [
        { name: 'backend', lead: 'Alice', members: ['Alice', 'Bob'] },
      ]),
    ]
    const result = buildTree(makeConfig(agents, depts), {}, [])
    const teamNodes = result.nodes.filter((n) => n.type === 'team')
    expect(teamNodes).toHaveLength(1)
    expect(teamNodes[0]!.id).toBe('team-engineering-backend')
    expect(teamNodes[0]!.parentId).toBe('dept-engineering')
  })

  it('parents team members to the team group node', () => {
    const agents = [
      makeAgent({ id: 'a1', name: 'Alice', department: 'engineering', level: 'lead' }),
      makeAgent({ id: 'a2', name: 'Bob', department: 'engineering', level: 'mid' }),
    ]
    const depts = [
      makeDept('engineering', 'Engineering', [
        { name: 'backend', lead: 'Alice', members: ['Alice', 'Bob'] },
      ]),
    ]
    const result = buildTree(makeConfig(agents, depts), {}, [])
    const bob = result.nodes.find((n) => n.id === 'a2')
    expect(bob?.parentId).toBe('team-engineering-backend')
  })

  it('does not emit team nodes when department has no teams', () => {
    const agents = [
      makeAgent({ id: 'a1', name: 'Alice', department: 'engineering', level: 'lead' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])
    const teamNodes = result.nodes.filter((n) => n.type === 'team')
    expect(teamNodes).toHaveLength(0)
  })

  it('agents not in any team stay parented to dept group', () => {
    const agents = [
      makeAgent({ id: 'a1', name: 'Alice', department: 'engineering', level: 'lead' }),
      makeAgent({ id: 'a2', name: 'Bob', department: 'engineering', level: 'mid' }),
      makeAgent({ id: 'a3', name: 'Carol', department: 'engineering', level: 'mid' }),
    ]
    const depts = [
      makeDept('engineering', 'Engineering', [
        { name: 'backend', lead: 'Alice', members: ['Alice', 'Bob'] },
      ]),
    ]
    const result = buildTree(makeConfig(agents, depts), {}, [])
    const carol = result.nodes.find((n) => n.id === 'a3')
    expect(carol?.parentId).toBe('dept-engineering')
  })
})

// ── Pure org-tree helpers ───────────────────────────────────
// buildOrgTree exercises these transitively; the cases below pin
// the branchy logic directly so a regression names the helper.

describe('humanizeDepartmentName', () => {
  const cases: ReadonlyArray<[string, string]> = [
    ['', ''],
    ['engineering', 'Engineering'],
    ['quality_assurance', 'Quality Assurance'],
    ['customer_success_ops', 'Customer Success Ops'],
    ['_', ' '],
    ['__', '  '],
  ]

  it.each(cases)('humanises %j -> %j', (raw, expected) => {
    expect(humanizeDepartmentName(raw)).toBe(expected)
  })
})

describe('findHighestSeniority', () => {
  it('returns null for an empty roster', () => {
    expect(findHighestSeniority([])).toBeNull()
  })

  it('returns the only agent in a single-member roster', () => {
    const solo = makeAgent({ name: 'Solo', department: 'engineering', level: 'mid' })
    expect(findHighestSeniority([solo])).toBe(solo)
  })

  it('picks the highest-ranked level regardless of array order', () => {
    const junior = makeAgent({ name: 'Jr', department: 'engineering', level: 'junior' })
    const director = makeAgent({ name: 'Dir', department: 'engineering', level: 'director' })
    const senior = makeAgent({ name: 'Sr', department: 'engineering', level: 'senior' })
    expect(findHighestSeniority([junior, director, senior])).toBe(director)
  })

  it('keeps the first agent on a seniority tie', () => {
    const first = makeAgent({ name: 'First', department: 'engineering', level: 'lead' })
    const second = makeAgent({ name: 'Second', department: 'engineering', level: 'lead' })
    expect(findHighestSeniority([first, second])).toBe(first)
  })
})

describe('findCeo', () => {
  it('returns null for an empty roster', () => {
    expect(findCeo([])).toBeNull()
  })

  it('prefers a c_suite agent in the executive department', () => {
    const otherCSuite = makeAgent({ name: 'CTO', department: 'engineering', level: 'c_suite' })
    const execCeo = makeAgent({ name: 'Exec', department: 'executive', level: 'c_suite' })
    expect(findCeo([otherCSuite, execCeo])).toBe(execCeo)
  })

  it('falls back to any c_suite agent when none sit in executive', () => {
    const ic = makeAgent({ name: 'IC', department: 'engineering', level: 'senior' })
    const cto = makeAgent({ name: 'CTO', department: 'engineering', level: 'c_suite' })
    expect(findCeo([ic, cto])).toBe(cto)
  })

  it('falls back to the highest-seniority agent when no c_suite exists', () => {
    const mid = makeAgent({ name: 'Mid', department: 'engineering', level: 'mid' })
    const vp = makeAgent({ name: 'VP', department: 'engineering', level: 'vp' })
    expect(findCeo([mid, vp])).toBe(vp)
  })
})
