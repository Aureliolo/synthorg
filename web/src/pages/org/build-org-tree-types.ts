import type { Node, Edge } from '@xyflow/react'
import type { DashboardAgentConfig } from '@/api/types/agents'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { CompanyConfig, DashboardDepartment } from '@/api/types/org'
import type { AgentRuntimeStatus } from '@/utils/agent-status'

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
  // Opaque wire identifier; the chart never uses it as a typed lookup key,
  // so it stays a plain string rather than a brand that needs casting.
  department: string
  runtimeStatus: AgentRuntimeStatus
  /**
   * True for the agent holding this department's head role --
   * rendered with a LEAD badge so the dept head is visually obvious.
   */
  isDeptLead?: boolean
  /**
   * True when this agent is the company CEO (the root of the
   * reporting graph).  Rendered with a subtle crown/accent so the top
   * of the company is visible even though there is no separate CEO
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
  departmentName: string
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
  departmentName: string
  leadName: string | undefined
  memberCount: number
  [key: string]: unknown
}

// ── Dept admin node dimensions ──────────────────────────────

export const DEPT_ADMIN_WIDTH = 200

// ── Owner / admin input ─────────────────────────────────────

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

// ── Public result + args ────────────────────────────────────

export interface OrgTree {
  nodes: Node[]
  edges: Edge[]
}

/**
 * Inputs for {@link buildOrgTree}, grouped into a single props
 * object so the builder stays within the parameter budget and new
 * operator context can be threaded without re-ordering positional
 * arguments.
 */
export interface BuildOrgTreeArgs {
  config: CompanyConfig
  runtimeStatuses: Record<string, AgentRuntimeStatus>
  departmentHealths: readonly DepartmentHealth[]
  owners?: readonly OwnerInfo[] | undefined
  deptAdmins?: readonly DeptAdminInfo[] | undefined
  currentUserId?: string | undefined
}

// ── Internal build accumulator ──────────────────────────────

/**
 * Mutable accumulator threaded through the per-section emission
 * helpers.  Holds the growing node/edge arrays plus the resolved
 * lookups (dept membership, health, CEO identity) every helper
 * needs, so each helper takes the context plus only its own extra
 * argument and stays within the parameter and complexity budgets.
 */
export interface BuildContext {
  nodes: Node[]
  edges: Edge[]
  deptAgents: Map<string, DashboardAgentConfig[]>
  healthMap: Map<string, DepartmentHealth>
  runtimeStatuses: Record<string, AgentRuntimeStatus>
  rootDept: DashboardDepartment | null
  ceo: DashboardAgentConfig | null
  ceoId: string | undefined
  ownerIds: string[]
}

// ── Pure org-position helpers ───────────────────────────────

/**
 * Resolve a department's head agent from its declared head role.
 *
 * Authority follows the reporting graph, so a department's head is the
 * agent holding its ``head`` role (case-insensitive), preferring an
 * explicit ``head_id`` match. A department with no declared head has no
 * head node -- returns ``null`` rather than inventing one.
 */
export function findDeptHead(
  dept: DashboardDepartment,
  members: readonly DashboardAgentConfig[],
): DashboardAgentConfig | null {
  if (members.length === 0) return null
  if (dept.head_id) {
    const byId = members.find((a) => a.id === dept.head_id)
    if (byId) return byId
  }
  if (dept.head) {
    const headRole = dept.head.toLowerCase()
    const byRole = members.find((a) => a.role.toLowerCase() === headRole)
    if (byRole) return byRole
  }
  return null
}

/**
 * Resolve the company CEO -- the root of the reporting graph. The CEO
 * is the agent whose role is ``CEO`` (case-insensitive); absent that, the
 * first agent in the executive department. Returns ``null`` when neither
 * is present.
 */
export function findCeo(
  agents: readonly DashboardAgentConfig[],
): DashboardAgentConfig | null {
  const [byRole] = agents.filter((a) => a.role.toLowerCase() === 'ceo')
  if (byRole) return byRole

  const [exec] = agents.filter((a) => a.department === 'executive')
  return exec ?? null
}
