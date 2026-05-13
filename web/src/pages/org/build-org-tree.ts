import type { Node, Edge } from '@xyflow/react'
import type { AgentConfig } from '@/api/types/agents'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { DepartmentName, SeniorityLevel } from '@/api/types/enums'
import type { CompanyConfig, Department } from '@/api/types/org'
import type { AgentRuntimeStatus } from '@/lib/utils'
import { resolveRuntimeStatus } from './status-mapping'

/**
 * Render a department's identifier in human-readable form.
 *
 * The backend stores department names as ``snake_case`` machine keys
 * (e.g. ``quality_assurance``). Without humanisation the org chart's
 * ``uppercase tracking-wide`` text styling on
 * ``DepartmentGroupNode`` renders the underscore verbatim
 * (``QUALITY_ASSURANCE``), which looks like a leaked enum value.
 * Replacing ``_`` with a space restores word boundaries
 * (``Quality Assurance`` -> ``QUALITY ASSURANCE``) so the upstream
 * CSS transform produces a real heading.
 */
export function humanizeDepartmentName(raw: string): string {
  if (!raw) return raw
  return raw
    .split('_')
    .map((seg) => {
      if (!seg) return seg
      const first = seg.charAt(0).toUpperCase()
      return first + seg.slice(1)
    })
    .join(' ')
}

// ── Node data interfaces ────────────────────────────────────

export interface OwnerNodeData {
  ownerId: string
  displayName: string
  role: 'owner'
  /** True when this owner node represents the currently logged-in user. */
  isCurrentUser?: boolean
  [key: string]: unknown
}

export interface AgentNodeData {
  agentId: string
  name: string
  role: string
  department: DepartmentName
  level: SeniorityLevel
  runtimeStatus: AgentRuntimeStatus
  /**
   * True for the highest-seniority member of this agent's
   * department -- rendered with a LEAD badge so the derived dept
   * head is visually obvious.
   */
  isDeptLead?: boolean
  /**
   * True when this agent is also the CEO of the company (the
   * highest-seniority c-suite member, usually in the executive
   * department).  Rendered with a subtle crown/accent so the top of
   * the company is visible even though there is no separate CEO
   * node anymore.
   */
  isCompanyCeo?: boolean
  [key: string]: unknown
}

export interface CeoNodeData extends AgentNodeData {
  companyName: string
  [key: string]: unknown
}

export interface DepartmentAgentStatusDot {
  agentId: string
  runtimeStatus: AgentRuntimeStatus
}

export interface DepartmentGroupData {
  departmentName: DepartmentName
  displayName: string
  agentCount: number
  activeCount: number
  budgetPercent: number | null
  utilizationPercent: number | null
  cost7d: number | null
  currency: string | null
  statusDots: DepartmentAgentStatusDot[]
  isEmpty: boolean
  /** True when this dept is the root of the chart (contains the CEO). */
  isRootDepartment?: boolean
  isCollapsed?: boolean
  onToggleCollapsed?: (deptId: string) => void
  isDropTarget?: boolean
  [key: string]: unknown
}

export interface TeamGroupData {
  teamName: string
  departmentName: DepartmentName
  leadName: string | undefined
  memberCount: number
  [key: string]: unknown
}

// ── Dept admin node dimensions ──────────────────────────────

export const DEPT_ADMIN_WIDTH = 200
export const DEPT_ADMIN_HEIGHT = 70

// ── Seniority ordering ──────────────────────────────────────

const SENIORITY_RANK: Record<SeniorityLevel, number> = {
  c_suite: 7,
  vp: 6,
  director: 5,
  principal: 4,
  lead: 3,
  senior: 2,
  mid: 1,
  junior: 0,
}

function seniorityOf(level: SeniorityLevel): number {
  return SENIORITY_RANK[level] ?? -1
}

// ── Owner input ─────────────────────────────────────────────

/**
 * Human operator info for synthesising owner nodes at the top of
 * the chart.  Multiple owners are rendered as a horizontal row
 * above the CEO's department.
 */
export interface OwnerInfo {
  id: string
  displayName: string
}

/**
 * Department admin info for rendering human admin nodes inside
 * their scoped department boxes.
 */
export interface DeptAdminInfo {
  id: string
  displayName: string
  department: string
}

// ── Core tree-building function ─────────────────────────────

export interface OrgTree {
  nodes: Node[]
  edges: Edge[]
}

/**
 * Build React Flow nodes and edges from a CompanyConfig.
 *
 * Hierarchy rendered top-to-bottom:
 *
 *   owner(s):   synthetic human node(s) at the very top
 *     └── root department box
 *           ├── CEO / CTO / highest c-suite agent    (inside the box)
 *           ├── other executive-tier agents          (inside the box)
 *           └── (other departments hang off the root dept box)
 *                   ├── dept A box
 *                   │     └── dept A agents
 *                   ├── dept B box
 *                   │     └── dept B agents
 *                   └── dept C box
 *                         └── dept C agents
 *
 * The "root department" is the department that contains the CEO
 * (usually `executive`).  Unlike the earlier design where the CEO
 * was extracted as its own standalone node, here the CEO lives
 * INSIDE its home department box and the box itself is the chart's
 * root.  That matches real-world org charts: "leadership" is a
 * real department, not a detached node floating above everything,
 * and avoids the "edge cuts through the executive box" problem
 * because inter-department lines now start from the root dept box's
 * bottom border (not from an agent buried inside it).
 *
 * Terminated agents are excluded.
 */
export function buildOrgTree(
  config: CompanyConfig,
  runtimeStatuses: Record<string, AgentRuntimeStatus>,
  departmentHealths: readonly DepartmentHealth[],
  owners: readonly OwnerInfo[] = [],
  deptAdmins: readonly DeptAdminInfo[] = [],
  currentUserId?: string,
): OrgTree {
  const agents = config.agents.filter((a) => (a.status ?? 'active') !== 'terminated')

  const healthMap = new Map(departmentHealths.map((h) => [h.department_name, h]))
  const nodes: Node[] = []
  const edges: Edge[] = []

  // Identify CEO (for the LEAD badge + root-dept selection)
  const ceo = findCeo(agents)
  const ceoId = ceo ? (ceo.id ?? ceo.name) : undefined
  const rootDeptName: DepartmentName | null = ceo ? ceo.department : null

  // Owner nodes (human operators)
  //
  // `width` and `height` are set explicitly so dagre (and the
  // post-layout centering pass in `layout.ts`) use the real card
  // size instead of the 160x80 default.  The OwnerNode component
  // renders at a fixed `w-[240px]` with a title row + an
  // avatar row, so 240x90 matches the rendered footprint exactly.
  // Without these, the centering pass would offset the owner by
  // ~20-40 px because it thought the card was narrower than it is.
  const OWNER_NODE_WIDTH = 240
  const OWNER_NODE_HEIGHT = 90
  const ownerIds: string[] = []
  for (const owner of owners) {
    const ownerNodeId = `owner-${owner.id}`
    ownerIds.push(ownerNodeId)
    nodes.push({
      id: ownerNodeId,
      type: 'owner',
      position: { x: 0, y: 0 },
      width: OWNER_NODE_WIDTH,
      height: OWNER_NODE_HEIGHT,
      data: {
        ownerId: owner.id,
        displayName: owner.displayName,
        role: 'owner',
        isCurrentUser: currentUserId != null && owner.id === currentUserId,
      } satisfies OwnerNodeData,
    })
  }

  // Group agents by department
  const deptAgents = new Map<DepartmentName, AgentConfig[]>()
  for (const agent of agents) {
    const list = deptAgents.get(agent.department) ?? []
    list.push(agent)
    deptAgents.set(agent.department, list)
  }

  // Resolve the effective department list (config + any synthesised
  // for agents whose dept isn't in config -- resilience for drift).
  const configuredDeptNames = new Set(config.departments.map((d) => d.name))
  const syntheticDepts: Department[] = []
  for (const deptName of deptAgents.keys()) {
    if (!configuredDeptNames.has(deptName)) {
      syntheticDepts.push({
        name: deptName,
        display_name: deptName,
        teams: [],
      })
    }
  }
  const allDepartments: readonly Department[] =
    syntheticDepts.length === 0
      ? config.departments
      : [...config.departments, ...syntheticDepts]

  // Reorder so the root department (the CEO's dept) is first.  The
  // reordering only affects iteration convenience; the actual rank
  // hierarchy is enforced by edges, not array order.
  const rootDept = rootDeptName
    ? allDepartments.find((d) => d.name === rootDeptName) ?? null
    : null
  const otherDepts = allDepartments.filter((d) => d !== rootDept)

  // Compute per-dept data (shared helper)
  const buildDeptData = (dept: Department): DepartmentGroupData => {
    const deptMembers = deptAgents.get(dept.name) ?? []
    const health = healthMap.get(dept.name)
    const activeCount = deptMembers.filter(
      (a) => resolveRuntimeStatus(a.id ?? a.name, a.status ?? 'active', runtimeStatuses) === 'active',
    ).length
    const budgetPercent = typeof dept.budget_percent === 'number' ? dept.budget_percent : null
    const cost7d = health?.department_cost_7d ?? null
    // Prefer the backend-computed utilization_percent which uses a
    // consistent time window (active_agent_count / agent_count * 100).
    // The previous client-side calculation divided 7-day cost by
    // monthly budget, mixing incompatible windows.
    const utilizationPercent = health?.utilization_percent ?? null
    const statusDots: DepartmentAgentStatusDot[] = deptMembers.map((a) => ({
      agentId: a.id ?? a.name,
      runtimeStatus: resolveRuntimeStatus(a.id ?? a.name, a.status ?? 'active', runtimeStatuses),
    }))
    return {
      departmentName: dept.name,
      displayName: dept.display_name ?? humanizeDepartmentName(dept.name),
      agentCount: deptMembers.length,
      activeCount,
      budgetPercent,
      utilizationPercent,
      cost7d,
      currency: health?.currency ?? null,
      statusDots,
      isEmpty: deptMembers.length === 0,
      isRootDepartment: dept === rootDept,
    }
  }

  // ── Emit the root department (if any) + its agents ───────
  if (rootDept) {
    const groupId = `dept-${rootDept.name}`
    nodes.push({
      id: groupId,
      type: 'department',
      position: { x: 0, y: 0 },
      data: buildDeptData(rootDept),
    })
    emitDeptChildren(nodes, edges, rootDept, deptAgents, runtimeStatuses, ceoId)

    // Owner → root dept edges, both visible and hidden-for-layout.
    // The visible one terminates at the dept box's top handle so the
    // line is clean.  The hidden one targets the root dept's head
    // agent to give dagre a rank constraint (dagre can't see the
    // dept group nodes, so without this edge it doesn't know where
    // to place the root's agents).
    const rootHead = findHighestSeniority(deptAgents.get(rootDept.name) ?? [])
    const rootHeadId = rootHead ? (rootHead.id ?? rootHead.name) : null
    for (const ownerNodeId of ownerIds) {
      edges.push({
        id: `e-${ownerNodeId}-${groupId}`,
        source: ownerNodeId,
        target: groupId,
        type: 'hierarchy',
      })
      if (rootHeadId) {
        edges.push({
          id: `e-layout-${ownerNodeId}-${rootHeadId}`,
          source: ownerNodeId,
          target: rootHeadId,
          type: 'hierarchy',
          hidden: true,
          // Tagged as 'owner-to-root'.  layout.ts computes a
          // dynamic minlen for this edge kind that accounts for
          // the root dept's top chrome (header + padding) but
          // NOT any source bottom chrome (owner is a standalone
          // card, not a dept box).
          data: { crossDeptKind: 'owner-to-root' },
        })
      }
    }
  }

  // ── Emit the other departments + their agents ────────────
  for (const dept of otherDepts) {
    const groupId = `dept-${dept.name}`
    nodes.push({
      id: groupId,
      type: 'department',
      position: { x: 0, y: 0 },
      data: buildDeptData(dept),
    })
    emitDeptChildren(nodes, edges, dept, deptAgents, runtimeStatuses, ceoId)

    // If there is a root dept, wire it to this one (both visible
    // edge to the box + hidden layout edge to the head agent).
    if (rootDept && ceo) {
      const rootGroupId = `dept-${rootDept.name}`
      edges.push({
        id: `e-${rootGroupId}-${groupId}`,
        source: rootGroupId,
        target: groupId,
        type: 'hierarchy',
      })
      const head = findHighestSeniority(deptAgents.get(dept.name) ?? [])
      const headId = head ? (head.id ?? head.name) : null
      if (headId && ceoId) {
        edges.push({
          id: `e-layout-${ceoId}-${headId}`,
          source: ceoId,
          target: headId,
          type: 'hierarchy',
          hidden: true,
          // Tagged as 'ceo-to-child'.  layout.ts computes a larger
          // dynamic minlen for this edge kind because the path
          // needs to clear BOTH the source root dept's bottom
          // chrome (padding + add-agent footer) AND the target
          // non-root dept's top chrome (header + padding).
          data: { crossDeptKind: 'ceo-to-child' },
        })
      }
    } else {
      // No root dept (no CEO detected) -- wire owner directly to
      // each dept box so the chart isn't disconnected.
      for (const ownerNodeId of ownerIds) {
        edges.push({
          id: `e-${ownerNodeId}-${groupId}`,
          source: ownerNodeId,
          target: groupId,
          type: 'hierarchy',
        })
      }
    }
  }

  // ── Emit dept admin nodes inside their scoped departments ──
  for (const admin of deptAdmins) {
    const deptLower = admin.department.toLowerCase()
    const matchedDept = allDepartments.find(
      (d) => d.name.toLowerCase() === deptLower,
    )
    if (!matchedDept) continue
    const adminNodeId = `dept-admin-${admin.id}`
    const groupId = `dept-${matchedDept.name}`
    nodes.push({
      id: adminNodeId,
      type: 'deptAdmin',
      position: { x: 0, y: 0 },
      parentId: groupId,
      extent: 'parent' as const,
      width: DEPT_ADMIN_WIDTH,
      height: DEPT_ADMIN_HEIGHT,
      data: {
        adminId: admin.id,
        displayName: admin.displayName,
        department: admin.department,
        role: 'department_admin',
      },
    })
  }

  return { nodes, edges }
}

/**
 * Emit agent nodes inside a given department box, plus the
 * head→member / team-lead→member edges that form the internal
 * structure of that department.  Extracted from the main function
 * for clarity -- both the root dept and the other depts share the
 * same internal-emission logic.
 */
function emitDeptChildren(
  nodes: Node[],
  edges: Edge[],
  dept: Department,
  deptAgents: Map<DepartmentName, AgentConfig[]>,
  runtimeStatuses: Record<string, AgentRuntimeStatus>,
  ceoId: string | undefined,
): void {
  const deptMembers = deptAgents.get(dept.name) ?? []
  const head = findHighestSeniority(deptMembers)
  const headId = head ? (head.id ?? head.name) : undefined
  const groupId = `dept-${dept.name}`

  // Build team membership: agentId -> teamGroupId
  const agentTeamGroup = new Map<string, string>()
  // Team lead map: agentId -> leadId (for intra-team edges)
  const teamMemberSet = new Map<string, string>()

  for (const team of dept.teams) {
    const teamGroupId = `team-${dept.name}-${team.name}`
    const teamMembers = deptMembers.filter((a) => team.members.includes(a.name))
    const teamLead = team.lead
      ? deptMembers.find((a) => a.name === team.lead) ?? findHighestSeniority(teamMembers)
      : findHighestSeniority(teamMembers)
    const teamLeadId = teamLead ? (teamLead.id ?? teamLead.name) : undefined

    // Emit team group node
    const teamData: TeamGroupData = {
      teamName: team.name,
      departmentName: dept.name,
      leadName: teamLead?.name,
      memberCount: teamMembers.length,
    }
    nodes.push({
      id: teamGroupId,
      type: 'team',
      position: { x: 0, y: 0 },
      parentId: groupId,
      data: teamData,
    })

    // Edge from dept head to team lead
    if (headId && teamLeadId && headId !== teamLeadId) {
      edges.push({
        id: `e-${headId}-${teamLeadId}`,
        source: headId,
        target: teamLeadId,
        type: 'hierarchy',
      })
    }

    for (const member of teamMembers) {
      const memberId = member.id ?? member.name
      agentTeamGroup.set(memberId, teamGroupId)
      if (teamLeadId && memberId !== teamLeadId && !teamMemberSet.has(memberId)) {
        teamMemberSet.set(memberId, teamLeadId)
      }
    }
  }

  for (const agent of deptMembers) {
    const agentId = agent.id ?? agent.name
    const runtimeStatus = resolveRuntimeStatus(agentId, agent.status ?? 'active', runtimeStatuses)

    const nodeData: AgentNodeData = {
      agentId,
      name: agent.name,
      role: agent.role,
      department: agent.department,
      level: agent.level,
      runtimeStatus,
      isDeptLead: headId != null && agentId === headId,
      isCompanyCeo: ceoId != null && agentId === ceoId,
    }

    // Parent to team group if assigned, otherwise to dept group
    const parentId = agentTeamGroup.get(agentId) ?? groupId

    nodes.push({
      id: agentId,
      type: 'agent',
      position: { x: 0, y: 0 },
      parentId,
      data: nodeData,
    })

    if (teamMemberSet.has(agentId)) {
      const leadId = teamMemberSet.get(agentId)!
      edges.push({
        id: `e-${leadId}-${agentId}`,
        source: leadId,
        target: agentId,
        type: 'hierarchy',
      })
    } else if (headId && agentId !== headId && !agentTeamGroup.has(agentId)) {
      // Only add dept-head-to-agent edge for unassigned agents
      edges.push({
        id: `e-${headId}-${agentId}`,
        source: headId,
        target: agentId,
        type: 'hierarchy',
      })
    }
  }
}

// ── Helper functions ────────────────────────────────────────

function findHighestSeniority(agents: readonly AgentConfig[]): AgentConfig | null {
  if (agents.length === 0) return null
  return agents.reduce((best, curr) =>
    seniorityOf(curr.level) > seniorityOf(best.level) ? curr : best,
  )
}

function findCeo(agents: readonly AgentConfig[]): AgentConfig | null {
  const execCSuite = agents.filter(
    (a) => a.department === 'executive' && a.level === 'c_suite',
  )
  if (execCSuite.length > 0) return execCSuite[0]!

  const cSuite = agents.filter((a) => a.level === 'c_suite')
  if (cSuite.length > 0) return cSuite[0]!

  return findHighestSeniority(agents)
}
