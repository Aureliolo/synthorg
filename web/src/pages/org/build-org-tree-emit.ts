import type { DashboardAgentConfig } from '@/api/types/agents'
import type { DepartmentName } from '@/api/types/enums'
import type { CompanyConfig, DashboardDepartment } from '@/api/types/org'
import { resolveRuntimeStatus } from './status-mapping'
import {
  type AgentNodeData,
  type BuildContext,
  type DepartmentAgentStatusDot,
  type DepartmentGroupData,
  type DeptAdminInfo,
  type OwnerInfo,
  type TeamGroupData,
  findCeo,
  findHighestSeniority,
  humanizeDepartmentName,
} from './build-org-tree-types'

/** Resolve the CEO, its node id, and the root department box. */
export function resolveRoot(
  agents: readonly DashboardAgentConfig[],
  allDepartments: readonly DashboardDepartment[],
): { ceo: DashboardAgentConfig | null; ceoId: string | undefined; rootDept: DashboardDepartment | null } {
  const ceo = findCeo(agents)
  const ceoId = ceo ? (ceo.id ?? ceo.name) : undefined
  const rootDeptName = ceo ? (ceo.department as DepartmentName) : null
  const rootDept = rootDeptName
    ? allDepartments.find((d) => d.name === rootDeptName) ?? null
    : null
  return { ceo, ceoId, rootDept }
}

// Owner node footprint.
//
// `width`/`height` are set explicitly so dagre (and the post-layout
// centering pass in `layout.ts`) use the real card size instead of
// the 160x80 default.  The OwnerNode component renders at a fixed
// `w-[240px]` with a title row + an avatar row, so 240x90 matches the
// rendered footprint exactly.  Without these, the centering pass
// would offset the owner by ~20-40 px because it thought the card was
// narrower than it is.
const OWNER_NODE_WIDTH = 240
const OWNER_NODE_HEIGHT = 90

/** Group agents by their department name. */
export function groupAgentsByDept(
  agents: readonly DashboardAgentConfig[],
): Map<string, DashboardAgentConfig[]> {
  const deptAgents = new Map<string, DashboardAgentConfig[]>()
  for (const agent of agents) {
    const list = deptAgents.get(agent.department) ?? []
    list.push(agent)
    deptAgents.set(agent.department, list)
  }
  return deptAgents
}

/**
 * Effective department list: the configured departments plus a
 * synthetic entry for any department an agent claims that the config
 * doesn't declare (resilience for config/agent drift).
 */
export function resolveDepartments(
  config: CompanyConfig,
  deptAgents: Map<string, DashboardAgentConfig[]>,
): readonly DashboardDepartment[] {
  const configuredDeptNames = new Set(config.departments.map((d) => d.name))
  const syntheticDepts: DashboardDepartment[] = []
  for (const deptName of deptAgents.keys()) {
    if (configuredDeptNames.has(deptName)) continue
    syntheticDepts.push({
      name: deptName,
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
    })
  }
  return syntheticDepts.length === 0
    ? config.departments
    : [...config.departments, ...syntheticDepts]
}

/** Compute the rendered group data for a single department. */
export function buildDeptData(
  dept: DashboardDepartment,
  ctx: BuildContext,
): DepartmentGroupData {
  const deptMembers = ctx.deptAgents.get(dept.name) ?? []
  const health = ctx.healthMap.get(dept.name)
  const activeCount = deptMembers.filter(
    (a) =>
      resolveRuntimeStatus(a.id ?? a.name, a.status ?? 'active', ctx.runtimeStatuses) ===
      'active',
  ).length
  const budgetPercent =
    typeof dept.budget_percent === 'number' ? dept.budget_percent : null
  const cost7d = health?.department_cost_7d ?? null
  // % currently working = runtime-active members / total members.
  // The backend's ``utilization_percent`` divides HR-status="active"
  // count by total -- which is 100% in a fresh install (every hired
  // agent has HR status "active" by default), so it reads as "all
  // departments at 100%" before any task has actually executed.  The
  // runtime status is WS-driven (idle until a task starts) and
  // matches operator intuition for "active right now".
  const utilizationPercent =
    deptMembers.length === 0
      ? null
      : Math.round((activeCount / deptMembers.length) * 100)
  const statusDots: DepartmentAgentStatusDot[] = deptMembers.map((a) => ({
    agentId: a.id ?? a.name,
    runtimeStatus: resolveRuntimeStatus(a.id ?? a.name, a.status ?? 'active', ctx.runtimeStatuses),
  }))
  return {
    departmentName: dept.name,
    displayName: humanizeDepartmentName(dept.name),
    agentCount: deptMembers.length,
    activeCount,
    budgetPercent,
    utilizationPercent,
    cost7d,
    currency: health?.currency ?? null,
    statusDots,
    isEmpty: deptMembers.length === 0,
    isRootDepartment: dept === ctx.rootDept,
  }
}

/** Emit the synthetic human-operator nodes at the top of the chart. */
export function emitOwnerNodes(
  ctx: BuildContext,
  owners: readonly OwnerInfo[],
  currentUserId: string | undefined,
): void {
  for (const owner of owners) {
    const ownerNodeId = `owner-${owner.id}`
    ctx.ownerIds.push(ownerNodeId)
    ctx.nodes.push({
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
      },
    })
  }
}

/** Emit the root department box, its agents, and the owner edges. */
export function emitRootDept(ctx: BuildContext, rootDept: DashboardDepartment): void {
  const groupId = `dept-${rootDept.name}`
  ctx.nodes.push({
    id: groupId,
    type: 'department',
    position: { x: 0, y: 0 },
    data: buildDeptData(rootDept, ctx),
  })
  emitDeptChildren(rootDept, ctx)

  // Owner -> root dept edges, both visible and hidden-for-layout.
  // The visible one terminates at the dept box's top handle so the
  // line is clean.  The hidden one targets the root dept's head agent
  // to give dagre a rank constraint (dagre can't see the dept group
  // nodes, so without this edge it doesn't know where to place the
  // root's agents).
  const rootHead = findHighestSeniority(ctx.deptAgents.get(rootDept.name) ?? [])
  const rootHeadId = rootHead ? (rootHead.id ?? rootHead.name) : null
  for (const ownerNodeId of ctx.ownerIds) {
    ctx.edges.push({
      id: `e-${ownerNodeId}-${groupId}`,
      source: ownerNodeId,
      target: groupId,
      type: 'hierarchy',
    })
    if (rootHeadId) {
      ctx.edges.push({
        id: `e-layout-${ownerNodeId}-${rootHeadId}`,
        source: ownerNodeId,
        target: rootHeadId,
        type: 'hierarchy',
        hidden: true,
        // Tagged 'owner-to-root'.  layout.ts computes a dynamic minlen
        // for this edge kind that accounts for the root dept's top
        // chrome (header + padding) but NOT any source bottom chrome
        // (owner is a standalone card, not a dept box).
        data: { crossDeptKind: 'owner-to-root' },
      })
    }
  }
}

/** Emit every non-root department box + its agents + parent edges. */
export function emitOtherDepts(
  ctx: BuildContext,
  allDepartments: readonly DashboardDepartment[],
): void {
  const otherDepts = allDepartments.filter((d) => d !== ctx.rootDept)
  for (const dept of otherDepts) {
    const groupId = `dept-${dept.name}`
    ctx.nodes.push({
      id: groupId,
      type: 'department',
      position: { x: 0, y: 0 },
      data: buildDeptData(dept, ctx),
    })
    emitDeptChildren(dept, ctx)
    wireDeptToParent(ctx, dept, groupId)
  }
}

/** Wire a non-root dept to the root (or directly to owners if no CEO). */
function wireDeptToParent(
  ctx: BuildContext,
  dept: DashboardDepartment,
  groupId: string,
): void {
  if (ctx.rootDept && ctx.ceo) {
    wireDeptToRoot(ctx, ctx.rootDept, dept, groupId)
    return
  }
  // No root dept (no CEO detected) -- wire owner directly to each
  // dept box so the chart isn't disconnected.
  for (const ownerNodeId of ctx.ownerIds) {
    ctx.edges.push({
      id: `e-${ownerNodeId}-${groupId}`,
      source: ownerNodeId,
      target: groupId,
      type: 'hierarchy',
    })
  }
}

function wireDeptToRoot(
  ctx: BuildContext,
  rootDept: DashboardDepartment,
  dept: DashboardDepartment,
  groupId: string,
): void {
  const rootGroupId = `dept-${rootDept.name}`
  ctx.edges.push({
    id: `e-${rootGroupId}-${groupId}`,
    source: rootGroupId,
    target: groupId,
    type: 'hierarchy',
  })
  const head = findHighestSeniority(ctx.deptAgents.get(dept.name) ?? [])
  const headId = head ? (head.id ?? head.name) : null
  if (headId && ctx.ceoId) {
    ctx.edges.push({
      id: `e-layout-${ctx.ceoId}-${headId}`,
      source: ctx.ceoId,
      target: headId,
      type: 'hierarchy',
      hidden: true,
      // Tagged 'ceo-to-child'.  layout.ts computes a larger dynamic
      // minlen for this kind because the path needs to clear BOTH the
      // source root dept's bottom chrome (padding + add-agent footer)
      // AND the target non-root dept's top chrome.
      data: { crossDeptKind: 'ceo-to-child' },
    })
  }
}

/** Emit dept-admin nodes scoped inside their matched department box. */
export function emitDeptAdmins(
  ctx: BuildContext,
  allDepartments: readonly DashboardDepartment[],
  deptAdmins: readonly DeptAdminInfo[],
): void {
  for (const admin of deptAdmins) {
    const deptLower = admin.department.toLowerCase()
    const matchedDept = allDepartments.find((d) => d.name.toLowerCase() === deptLower)
    if (!matchedDept) continue
    ctx.nodes.push({
      id: `dept-admin-${admin.id}`,
      type: 'deptAdmin',
      position: { x: 0, y: 0 },
      parentId: `dept-${matchedDept.name}`,
      extent: 'parent' as const,
      width: 200,
      height: 70,
      data: {
        adminId: admin.id,
        displayName: admin.displayName,
        department: admin.department,
        role: 'department_admin',
      },
    })
  }
}

// ── Dept internals (teams + agents) ─────────────────────────

interface DeptEmitState {
  dept: DashboardDepartment
  deptMembers: DashboardAgentConfig[]
  groupId: string
  headId: string | undefined
  agentTeamGroup: Map<string, string>
  teamLeadOf: Map<string, string>
}

/**
 * Emit agent nodes inside a given department box, plus the
 * head->member / team-lead->member edges that form its internal
 * structure.  Shared by the root dept and the other depts.
 */
export function emitDeptChildren(dept: DashboardDepartment, ctx: BuildContext): void {
  const deptMembers = ctx.deptAgents.get(dept.name) ?? []
  const head = findHighestSeniority(deptMembers)
  const state: DeptEmitState = {
    dept,
    deptMembers,
    groupId: `dept-${dept.name}`,
    headId: head ? (head.id ?? head.name) : undefined,
    agentTeamGroup: new Map(),
    teamLeadOf: new Map(),
  }
  emitTeamGroups(ctx, state)
  emitDeptAgents(ctx, state)
}

function resolveTeamLead(
  team: DashboardDepartment['teams'][number],
  teamMembers: readonly DashboardAgentConfig[],
  deptMembers: readonly DashboardAgentConfig[],
): DashboardAgentConfig | null {
  if (team.lead) {
    return deptMembers.find((a) => a.name === team.lead) ?? findHighestSeniority(teamMembers)
  }
  return findHighestSeniority(teamMembers)
}

function emitTeamGroups(ctx: BuildContext, state: DeptEmitState): void {
  const { dept, deptMembers, groupId, headId, agentTeamGroup, teamLeadOf } = state
  for (const team of dept.teams) {
    const teamGroupId = `team-${dept.name}-${team.name}`
    const teamMembers = deptMembers.filter((a) => team.members.includes(a.name))
    const teamLead = resolveTeamLead(team, teamMembers, deptMembers)
    const teamLeadId = teamLead ? (teamLead.id ?? teamLead.name) : undefined

    ctx.nodes.push({
      id: teamGroupId,
      type: 'team',
      position: { x: 0, y: 0 },
      parentId: groupId,
      data: {
        teamName: team.name,
        departmentName: dept.name,
        leadName: teamLead?.name,
        memberCount: teamMembers.length,
      } satisfies TeamGroupData,
    })

    if (headId && teamLeadId && headId !== teamLeadId) {
      ctx.edges.push({
        id: `e-${headId}-${teamLeadId}`,
        source: headId,
        target: teamLeadId,
        type: 'hierarchy',
      })
    }

    mapTeamMembers(teamMembers, teamGroupId, teamLeadId, agentTeamGroup, teamLeadOf)
  }
}

function mapTeamMembers(
  teamMembers: readonly DashboardAgentConfig[],
  teamGroupId: string,
  teamLeadId: string | undefined,
  agentTeamGroup: Map<string, string>,
  teamLeadOf: Map<string, string>,
): void {
  for (const member of teamMembers) {
    const memberId = member.id ?? member.name
    agentTeamGroup.set(memberId, teamGroupId)
    if (teamLeadId && memberId !== teamLeadId && !teamLeadOf.has(memberId)) {
      teamLeadOf.set(memberId, teamLeadId)
    }
  }
}

function emitDeptAgents(ctx: BuildContext, state: DeptEmitState): void {
  const { deptMembers, groupId, headId, agentTeamGroup } = state
  for (const agent of deptMembers) {
    const agentId = agent.id ?? agent.name
    const runtimeStatus = resolveRuntimeStatus(
      agentId,
      agent.status ?? 'active',
      ctx.runtimeStatuses,
    )
    const nodeData: AgentNodeData = {
      agentId,
      name: agent.name,
      role: agent.role,
      department: agent.department as DepartmentName,
      level: agent.level,
      runtimeStatus,
      isDeptLead: headId != null && agentId === headId,
      isCompanyCeo: ctx.ceoId != null && agentId === ctx.ceoId,
    }
    ctx.nodes.push({
      id: agentId,
      type: 'agent',
      position: { x: 0, y: 0 },
      parentId: agentTeamGroup.get(agentId) ?? groupId,
      data: nodeData,
    })
    emitAgentEdge(ctx, agentId, state)
  }
}

function emitAgentEdge(ctx: BuildContext, agentId: string, state: DeptEmitState): void {
  const { headId, agentTeamGroup, teamLeadOf } = state
  const leadId = teamLeadOf.get(agentId)
  if (leadId) {
    ctx.edges.push({
      id: `e-${leadId}-${agentId}`,
      source: leadId,
      target: agentId,
      type: 'hierarchy',
    })
    return
  }
  // Only add a dept-head-to-agent edge for unassigned agents.
  if (headId && agentId !== headId && !agentTeamGroup.has(agentId)) {
    ctx.edges.push({
      id: `e-${headId}-${agentId}`,
      source: headId,
      target: agentId,
      type: 'hierarchy',
    })
  }
}
