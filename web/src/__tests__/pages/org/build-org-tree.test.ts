import { describe, expect, it } from 'vitest'
import { buildOrgTree } from '@/pages/org/build-org-tree'
import {
  findCeo,
  findDeptHead,
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
import type { CompanyConfig, DashboardDepartment } from '@/api/types/org'
import type { AgentRuntimeStatus } from '@/utils/agent-status'

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
    id: overrides.id ?? overrides.name,
    role: 'Developer',
    department: 'engineering',
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
    model: { provider: 'test-provider', model_id: 'test-basic-001', temperature: 0.7, max_tokens: 4096 },
    memory: { type: 'session', retention_days: null },
    tools: { access_level: 'standard', allowed: [], denied: [] },
    authority: {},
    autonomy_level: null,
    strategic_output_mode: null,
    personality_preset: null,
    capability: null,
    model_requirement: null,
    model_capabilities: null,
    model_capability_status: 'unresolved',
    hiring_date: '2026-01-01',
    ...overrides,
  }
}

function makeDept(
  name: string,
  displayName: string,
  teams: { name: string; lead: string; members: readonly string[] }[] = [],
  head: string | null = null,
): CompanyConfig['departments'][number] {
  return {
    name,
    display_name: displayName,
    autonomy_level: null,
    budget_percent: 0,
    head,
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
    departments: departments ?? deptNames.map((name) => makeDept(
      name,
      name.charAt(0).toUpperCase() + name.slice(1),
    )),
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
    total_runs: 10,
    task_success_rate: utilizationPercent / 100,
    utilization_percent: utilizationPercent,
    utilization_degraded: false,
    health_score: utilizationPercent,
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

  it('marks the CEO-role agent in the executive department as the company CEO', () => {
    const agents = [
      makeAgent({ id: 'ceo-1', name: 'Alice', role: 'CEO', department: 'executive' }),
      makeAgent({ id: 'dev-1', name: 'Bob', department: 'engineering' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const ceo = findCompanyCeo(result.nodes)
    expect(ceo).toBeDefined()
    expect((ceo!.data as AgentNodeData).name).toBe('Alice')
    expect(ceo!.parentId).toBe('dept-executive')
  })

  it('finds the CEO-role agent regardless of department', () => {
    const agents = [
      makeAgent({ id: 'cto', name: 'CTO', role: 'CEO', department: 'engineering' }),
      makeAgent({ id: 'dev', name: 'Dev', department: 'engineering' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const ceo = findCompanyCeo(result.nodes)
    expect(ceo).toBeDefined()
    expect((ceo!.data as AgentNodeData).name).toBe('CTO')
    expect(ceo!.parentId).toBe('dept-engineering')
  })

  it('falls back to an executive-department agent when no CEO role exists', () => {
    const agents = [
      makeAgent({ id: 'lead-1', name: 'Carol', role: 'Director', department: 'executive' }),
      makeAgent({ id: 'jr-1', name: 'Dave', department: 'engineering' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const ceo = findCompanyCeo(result.nodes)
    expect(ceo).toBeDefined()
    expect((ceo!.data as AgentNodeData).name).toBe('Carol')
  })

  it('marks the CEO\'s home department as the root department', () => {
    const agents = [
      makeAgent({ id: 'ceo', name: 'CEO', role: 'CEO', department: 'executive' }),
      makeAgent({ id: 'cto', name: 'CTO', role: 'CTO', department: 'engineering' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const execDept = result.nodes.find((n) => n.id === 'dept-executive')
    expect(execDept).toBeDefined()
    expect((execDept!.data as DepartmentGroupData).isRootDepartment).toBe(true)

    const engDept = result.nodes.find((n) => n.id === 'dept-engineering')
    expect(engDept).toBeDefined()
    // Engineering is NOT the root: the CEO lives in executive.
    expect((engDept!.data as DepartmentGroupData).isRootDepartment).toBe(false)
  })

  it('groups agents by department via parentId', () => {
    const agents = [
      makeAgent({ id: 'a1', name: 'A1', department: 'engineering' }),
      makeAgent({ id: 'a2', name: 'A2', department: 'engineering' }),
      makeAgent({ id: 'a3', name: 'A3', department: 'product' }),
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
      makeAgent({ id: 'ceo', name: 'CEO', role: 'CEO', department: 'executive' }),
      makeAgent({ id: 'cto', name: 'CTO', role: 'CTO', department: 'engineering' }),
      makeAgent({ id: 'cpo', name: 'CPO', role: 'CPO', department: 'product' }),
    ]
    const depts = [
      makeDept('executive', 'Executive', [], 'CEO'),
      makeDept('engineering', 'Engineering', [], 'CTO'),
      makeDept('product', 'Product', [], 'CPO'),
    ]
    const result = buildTree(makeConfig(agents, depts), {}, [])

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
      makeAgent({ id: 'ceo', name: 'CEO', role: 'CEO', department: 'executive' }),
    ]
    const depts = [makeDept('executive', 'Executive', [], 'CEO')]
    const result = buildTree(makeConfig(agents, depts), {}, [], makeOwners())

    const ownerNode = result.nodes.find((n) => n.type === 'owner')
    expect(ownerNode).toBeDefined()
    expect(ownerNode!.id).toBe('owner-owner-1')

    const visibleOwnerEdges = result.edges.filter((e) => e.source === 'owner-owner-1' && !e.hidden)
    expect(visibleOwnerEdges.map((e) => e.target)).toEqual(['dept-executive'])

    const hiddenOwnerEdges = result.edges.filter((e) => e.source === 'owner-owner-1' && e.hidden === true)
    expect(hiddenOwnerEdges.map((e) => e.target)).toEqual(['ceo'])
  })

  it('resolves the department head by role, first matching agent wins', () => {
    const agents = [
      makeAgent({ id: 'ceo', name: 'CEO', role: 'CEO', department: 'executive' }),
      makeAgent({ id: 'vp-a', name: 'VP Alpha', role: 'VP', department: 'engineering' }),
      makeAgent({ id: 'vp-b', name: 'VP Beta', role: 'VP', department: 'engineering' }),
      makeAgent({ id: 'vp-c', name: 'VP Gamma', role: 'VP', department: 'engineering' }),
    ]
    const depts = [
      makeDept('executive', 'Executive', [], 'CEO'),
      makeDept('engineering', 'Engineering', [], 'VP'),
    ]
    const result = buildTree(makeConfig(agents, depts), {}, [])

    // Visible edge: root dept box (executive) → engineering box
    const rootOut = result.edges.filter((e) => e.source === 'dept-executive' && !e.hidden)
    expect(rootOut.map((e) => e.target)).toEqual(['dept-engineering'])

    // Hidden layout edge: CEO → vp-a (engineering's dept head, the
    // first agent whose role matches the department head role)
    const hiddenCeoEdges = result.edges.filter((e) => e.source === 'ceo' && e.hidden === true)
    expect(hiddenCeoEdges.map((e) => e.target)).toEqual(['vp-a'])

    // Inside engineering: vp-a is dept head; vp-b and vp-c report to vp-a
    const headEdges = result.edges.filter((e) => e.source === 'vp-a')
    expect(headEdges.map((e) => e.target).sort()).toEqual(['vp-b', 'vp-c'])
  })

  it('creates internal head→member edges within a department', () => {
    const agents = [
      makeAgent({ id: 'lead', name: 'Lead', role: 'Lead', department: 'engineering' }),
      makeAgent({ id: 'dev1', name: 'Dev1', department: 'engineering' }),
      makeAgent({ id: 'dev2', name: 'Dev2', department: 'engineering' }),
    ]
    const depts = [makeDept('engineering', 'Engineering', [], 'Lead')]
    const result = buildTree(makeConfig(agents, depts), {}, [])

    // `lead` is the head of engineering; it emits edges to the other
    // two members.
    const leadEdges = result.edges.filter((e) => e.source === 'lead' && !e.hidden)
    expect(leadEdges.map((e) => e.target).sort()).toEqual(['dev1', 'dev2'])
  })

  it('excludes terminated agents from the chart', () => {
    const agents = [
      makeAgent({ id: 'active-1', name: 'Active', department: 'engineering', status: 'active' }),
      makeAgent({ id: 'fired-1', name: 'Fired', department: 'engineering', status: 'terminated' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    const agentNodes = result.nodes.filter((n) => n.type === 'agent')
    expect(agentNodes).toHaveLength(1)
    expect((agentNodes[0]!.data as AgentNodeData).name).toBe('Active')
  })

  it('creates department group nodes with health data', () => {
    const agents = [
      makeAgent({ id: 'a1', name: 'Dev', department: 'engineering' }),
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
      makeAgent({ id: 'a1', name: 'Dev', department: 'engineering', status: 'active' }),
    ]
    const result = buildTree(makeConfig(agents), { a1: 'error' }, [])

    const agentNode = result.nodes.find((n) => n.id === 'a1')
    expect((agentNode!.data as AgentNodeData).runtimeStatus).toBe('error')
  })

  it('uses team structure to derive reporting hierarchy', () => {
    const agents = [
      makeAgent({ id: 'lead', name: 'Lead', department: 'engineering' }),
      makeAgent({ id: 'sr', name: 'Senior', department: 'engineering' }),
      makeAgent({ id: 'jr', name: 'Junior', department: 'engineering' }),
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
      makeAgent({ id: 'a1', name: 'Dev', department: 'engineering' }),
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
      makeAgent({ id: 'ceo', name: 'CEO', role: 'CEO', department: 'executive' }),
      makeAgent({ id: 'dev', name: 'Dev', department: 'engineering' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [], makeOwners())

    const types = result.nodes.map((n) => n.type).sort()
    // owner + 2 dept boxes + 2 agents (CEO + dev) = 5 nodes, no 'ceo' type
    expect(types).toEqual(['agent', 'agent', 'department', 'department', 'owner'])
  })

  it('returns null cost / currency when no health data provided', () => {
    const agents = [
      makeAgent({ id: 'a1', name: 'Dev', department: 'engineering' }),
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
      makeAgent({ id: 'ceo', name: 'CEO', role: 'CEO', department: 'executive' }),
      makeAgent({ id: 'cto', name: 'CTO', role: 'CTO', department: 'engineering' }),
      makeAgent({ id: 'dev', name: 'Dev', department: 'engineering' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])

    for (const edge of result.edges) {
      expect(edge.type).toBe('hierarchy')
    }
  })


  it('treats agent without status as active (not filtered out)', () => {
    const { status: omitStatus, ...activeDefault } = makeAgent({
      id: 'a1',
      name: 'ActiveDefault',
      department: 'engineering',
    })
    void omitStatus
    const agents = [
      makeAgent({ id: 'a0', name: 'Lead', department: 'engineering' }),
      activeDefault,
      makeAgent({ id: 'a2', name: 'Terminated', department: 'engineering', status: 'terminated' }),
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
      makeAgent({ id: 'a1', name: 'Alice', department: 'engineering' }),
      makeAgent({ id: 'a2', name: 'Bob', department: 'engineering' }),
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
      makeAgent({ id: 'a1', name: 'Alice', department: 'engineering' }),
      makeAgent({ id: 'a2', name: 'Bob', department: 'engineering' }),
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
      makeAgent({ id: 'a1', name: 'Alice', department: 'engineering' }),
    ]
    const result = buildTree(makeConfig(agents), {}, [])
    const teamNodes = result.nodes.filter((n) => n.type === 'team')
    expect(teamNodes).toHaveLength(0)
  })

  it('agents not in any team stay parented to dept group', () => {
    const agents = [
      makeAgent({ id: 'a1', name: 'Alice', department: 'engineering' }),
      makeAgent({ id: 'a2', name: 'Bob', department: 'engineering' }),
      makeAgent({ id: 'a3', name: 'Carol', department: 'engineering' }),
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

describe('findDeptHead', () => {
  const engineering: DashboardDepartment = makeDept('engineering', 'Engineering', [], 'Lead')

  it('returns null for an empty roster', () => {
    expect(findDeptHead(engineering, [])).toBeNull()
  })

  it('returns the agent whose role matches the department head role', () => {
    const lead = makeAgent({ name: 'Lead', role: 'Lead', department: 'engineering' })
    const dev = makeAgent({ name: 'Dev', role: 'Developer', department: 'engineering' })
    expect(findDeptHead(engineering, [dev, lead])).toBe(lead)
  })

  it('returns null when no member holds the department head role', () => {
    const dev = makeAgent({ name: 'Dev', role: 'Developer', department: 'engineering' })
    expect(findDeptHead(engineering, [dev])).toBeNull()
  })

  it('prefers an explicit head_id over the head role', () => {
    const byId: DashboardDepartment = { ...engineering, head_id: 'dev' }
    const lead = makeAgent({ id: 'lead', name: 'Lead', role: 'Lead', department: 'engineering' })
    const dev = makeAgent({ id: 'dev', name: 'Dev', role: 'Developer', department: 'engineering' })
    expect(findDeptHead(byId, [lead, dev])).toBe(dev)
  })
})

describe('findCeo', () => {
  it('returns null for an empty roster', () => {
    expect(findCeo([])).toBeNull()
  })

  it('prefers the agent whose role is CEO', () => {
    const cto = makeAgent({ name: 'CTO', role: 'CTO', department: 'engineering' })
    const ceo = makeAgent({ name: 'Chief', role: 'CEO', department: 'engineering' })
    expect(findCeo([cto, ceo])).toBe(ceo)
  })

  it('falls back to the first executive-department agent when no CEO role exists', () => {
    const dev = makeAgent({ name: 'Dev', role: 'Developer', department: 'engineering' })
    const exec = makeAgent({ name: 'Exec', role: 'Director', department: 'executive' })
    expect(findCeo([dev, exec])).toBe(exec)
  })

  it('returns null when there is neither a CEO role nor an executive department', () => {
    const dev = makeAgent({ name: 'Dev', role: 'Developer', department: 'engineering' })
    expect(findCeo([dev])).toBeNull()
  })
})
