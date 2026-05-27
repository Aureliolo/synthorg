import {
  type BuildContext,
  type BuildOrgTreeArgs,
  type OrgTree,
} from './build-org-tree-types'
import {
  emitDeptAdmins,
  emitOtherDepts,
  emitOwnerNodes,
  emitRootDept,
  groupAgentsByDept,
  resolveDepartments,
  resolveRoot,
} from './build-org-tree-emit'

// Re-export the public surface so the many sibling node components
// (AgentNode, DepartmentGroupNode, etc.) and tests keep importing
// types and constants from this module unchanged.
export { DEPT_ADMIN_WIDTH } from './build-org-tree-types'
export type {
  AgentNodeData,
  BuildOrgTreeArgs,
  CeoNodeData,
  DepartmentAgentStatusDot,
  DepartmentGroupData,
  DeptAdminInfo,
  OrgTree,
  OwnerInfo,
  OwnerNodeData,
  TeamGroupData,
} from './build-org-tree-types'

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
 *                   ├── dept A box -> dept A agents
 *                   ├── dept B box -> dept B agents
 *                   └── dept C box -> dept C agents
 *
 * The "root department" is the department that contains the CEO
 * (usually `executive`).  The CEO lives INSIDE its home department
 * box and the box itself is the chart's root, so inter-department
 * lines start from the root dept box's bottom border rather than
 * cutting through an agent buried inside it.  Terminated agents are
 * excluded.
 */
export function buildOrgTree(args: BuildOrgTreeArgs): OrgTree {
  const {
    config,
    runtimeStatuses,
    departmentHealths,
    owners = [],
    deptAdmins = [],
    currentUserId,
  } = args

  const agents = config.agents.filter((a) => (a.status ?? 'active') !== 'terminated')
  const deptAgents = groupAgentsByDept(agents)
  const allDepartments = resolveDepartments(config, deptAgents)
  const { ceo, ceoId, rootDept } = resolveRoot(agents, allDepartments)

  const ctx: BuildContext = {
    nodes: [],
    edges: [],
    deptAgents,
    healthMap: new Map(departmentHealths.map((h) => [h.department_name, h])),
    runtimeStatuses,
    rootDept,
    ceo,
    ceoId,
    ownerIds: [],
  }

  emitOwnerNodes(ctx, owners, currentUserId)
  if (rootDept) emitRootDept(ctx, rootDept)
  emitOtherDepts(ctx, allDepartments)
  emitDeptAdmins(ctx, allDepartments, deptAdmins)

  return { nodes: ctx.nodes, edges: ctx.edges }
}
