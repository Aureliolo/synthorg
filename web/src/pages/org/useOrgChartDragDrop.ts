import { useCallback, useMemo, useRef, useState } from 'react'
import type { Node, OnNodeDrag } from '@xyflow/react'
import { useCompanyStore } from '@/stores/company'
import { useToastStore } from '@/stores/toast'
import { isDepartmentName, type DepartmentName } from '@/api/types/enums'
import type { AgentNodeData, DepartmentGroupData } from './build-org-tree'
import { findDropTarget, type DepartmentBounds } from './drop-target'
import type { ViewMode } from './OrgChartToolbar'

const AGENT_NODE_WIDTH = 160
const AGENT_NODE_HEIGHT = 80

// Discriminate on node.type to narrow node.data, which @xyflow/react types as
// an untyped record. The runtime guarantee is that 'agent'/'department' nodes
// carry the matching data shape, so these predicates replace inline `as` casts.
function isAgentNode(n: Node): n is Node & { data: AgentNodeData } {
  return n.type === 'agent'
}

function isDepartmentNode(n: Node): n is Node & { data: DepartmentGroupData } {
  return n.type === 'department'
}

type AddToast = ReturnType<typeof useToastStore.getState>['add']

export interface OrgChartDragDropResult {
  dragOverDeptId: string | null
  handleNodeDragStart: OnNodeDrag<Node>
  handleNodeDrag: OnNodeDrag<Node>
  handleNodeDragStop: OnNodeDrag<Node>
}

interface UseOrgChartDragDropArgs {
  viewMode: ViewMode
  displayNodes: Node[]
  announce: (msg: string) => void
}

/** Centre point of a dragged node, falling back to default agent size. */
function nodeCenter(node: Node): { x: number; y: number } {
  return {
    x: node.position.x + (node.measured?.width ?? AGENT_NODE_WIDTH) / 2,
    y: node.position.y + (node.measured?.height ?? AGENT_NODE_HEIGHT) / 2,
  }
}

interface ReassignArgs {
  agentId: string
  agentName: string
  originalDept: string
  newDept: string
  newDeptName: DepartmentName
  announce: (msg: string) => void
  addToast: AddToast
}

/**
 * Optimistically reassign an agent to a new department, then persist.
 * ``updateAgent`` owns its own error toast and returns ``null`` on
 * failure (sentinel-return contract; never throws, never wrapped in
 * try/catch). On the null branch we roll the optimistic reorder back
 * and announce the rollback so screen readers know the move did not
 * stick; on success a drag-and-drop move gets its own verbal cue +
 * toast even though the store already emits an "updated" toast.
 */
function reassignAgent(args: ReassignArgs): void {
  const store = useCompanyStore.getState()
  const rollback = store.optimisticReassignAgent(args.agentId, args.newDeptName)
  const existingAgent = store.config?.agents.find((a) => a.id === args.agentId)
  void useCompanyStore
    .getState()
    .updateAgent(args.agentId, {
      department: args.newDeptName,
      autonomy_level: existingAgent?.autonomy_level ?? null,
      level: existingAgent?.level ?? null,
    })
    .then((result) => onReassignSettled(result, args, rollback))
}

function onReassignSettled(
  result: unknown,
  args: ReassignArgs,
  rollback: () => void,
): void {
  if (result === null) {
    rollback()
    const currentDept = useCompanyStore
      .getState()
      .config?.agents.find((a) => a.id === args.agentId)?.department
    args.announce(
      currentDept === args.originalDept
        ? `Failed to move ${args.agentName}, returned to ${args.originalDept}`
        : `Failed to move ${args.agentName}`,
    )
    return
  }
  args.announce(`Moved ${args.agentName} to ${args.newDept}`)
  args.addToast({ variant: 'success', title: `Moved ${args.agentName} to ${args.newDept}` })
}

/** Drop-target hit boxes derived from the rendered department group nodes. */
function computeDeptBounds(displayNodes: Node[]): DepartmentBounds[] {
  return displayNodes
    .filter(isDepartmentNode)
    .map((n) => ({
      departmentName: n.data.departmentName,
      nodeId: n.id,
      x: n.position.x,
      y: n.position.y,
      width: (n.measured?.width ?? n.width ?? 200),
      height: (n.measured?.height ?? n.height ?? 120),
    }))
}

export function useOrgChartDragDrop(args: UseOrgChartDragDropArgs): OrgChartDragDropResult {
  const { viewMode, displayNodes, announce } = args
  const addToast = useToastStore((s) => s.add)

  const [dragOverDeptId, setDragOverDeptId] = useState<string | null>(null)
  const dragOverDeptIdRef = useRef<string | null>(null)
  const dragOriginalDeptRef = useRef<string | null>(null)

  const deptBounds = useMemo<DepartmentBounds[]>(
    () => computeDeptBounds(displayNodes),
    [displayNodes],
  )

  const handleNodeDragStart = useCallback(
    (_event: MouseEvent | TouchEvent, node: Node) => {
      if (!isAgentNode(node)) return
      if (viewMode !== 'hierarchy') return
      dragOriginalDeptRef.current = node.data.department
      announce(`Started dragging ${node.data.name}`)
    },
    [viewMode, announce],
  )

  const handleNodeDrag = useCallback(
    (_event: MouseEvent | TouchEvent, node: Node) => {
      if (!dragOriginalDeptRef.current) return
      const target = findDropTarget(nodeCenter(node), deptBounds)
      const newOverId = target?.nodeId ?? null
      const shouldAnnounce = dragOverDeptIdRef.current !== newOverId && target
      dragOverDeptIdRef.current = newOverId
      setDragOverDeptId(newOverId)
      if (shouldAnnounce) {
        queueMicrotask(() => announce(`Over ${target.departmentName}`))
      }
    },
    [deptBounds, announce],
  )

  const handleNodeDragStop = useCallback(
    (_event: MouseEvent | TouchEvent, node: Node) => {
      const originalDept = dragOriginalDeptRef.current
      dragOriginalDeptRef.current = null
      dragOverDeptIdRef.current = null
      setDragOverDeptId(null)

      if (!originalDept) return
      if (!isAgentNode(node)) return

      const target = findDropTarget(nodeCenter(node), deptBounds)
      const agentId = node.data.agentId
      const agentName = node.data.name
      const newDept = target?.departmentName

      if (!newDept || newDept === originalDept) {
        announce(`Cancelled moving ${agentName}`)
        return
      }
      if (!isDepartmentName(newDept)) {
        announce(`Failed to move ${agentName}`)
        addToast({
          variant: 'error',
          title: 'Reassignment failed',
          description: 'Invalid department target',
        })
        return
      }

      reassignAgent({ agentId, agentName, originalDept, newDept, newDeptName: newDept, announce, addToast })
    },
    [deptBounds, addToast, announce],
  )

  return { dragOverDeptId, handleNodeDragStart, handleNodeDrag, handleNodeDragStop }
}
