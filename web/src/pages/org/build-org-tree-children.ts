import type { DashboardAgentConfig } from '@/api/types/agents'
import type { DepartmentName } from '@/api/types/enums'
import type { DashboardDepartment } from '@/api/types/org'
import { resolveRuntimeStatus } from './status-mapping'
import {
  type AgentNodeData,
  type BuildContext,
  type TeamGroupData,
  findHighestSeniority,
} from './build-org-tree-types'

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
    headId: head ? head.id : undefined,
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
    const teamLeadId = teamLead ? teamLead.id : undefined

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
      // Include the team name so head -> lead edges stay unique when
      // several teams in a department share the same lead.
      ctx.edges.push({
        id: `e-${headId}-${teamLeadId}-${team.name}`,
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
    const memberId = member.id
    agentTeamGroup.set(memberId, teamGroupId)
    if (teamLeadId && memberId !== teamLeadId && !teamLeadOf.has(memberId)) {
      teamLeadOf.set(memberId, teamLeadId)
    }
  }
}

function emitDeptAgents(ctx: BuildContext, state: DeptEmitState): void {
  const { deptMembers, groupId, headId, agentTeamGroup } = state
  for (const agent of deptMembers) {
    const agentId = agent.id
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
