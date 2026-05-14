import { useCallback, useMemo, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import type { Node } from '@xyflow/react'
import { useCompanyStore } from '@/stores/company'
import { useToastStore } from '@/stores/toast'
import { isDepartmentName, type DepartmentName } from '@/api/types/enums'
import type { AgentNodeData } from './build-org-tree'
import { findDropTarget, type DepartmentBounds } from './drop-target'
import type { ViewMode } from './OrgChartToolbar'

const AGENT_NODE_WIDTH = 160
const AGENT_NODE_HEIGHT = 80

export interface OrgChartDragDropResult {
  dragOverDeptId: string | null
  handleNodeDragStart: (event: ReactMouseEvent, node: Node) => void
  handleNodeDrag: (event: ReactMouseEvent, node: Node) => void
  handleNodeDragStop: (event: ReactMouseEvent, node: Node) => void
}

interface UseOrgChartDragDropArgs {
  viewMode: ViewMode
  displayNodes: Node[]
  announce: (msg: string) => void
}

export function useOrgChartDragDrop(args: UseOrgChartDragDropArgs): OrgChartDragDropResult {
  const { viewMode, displayNodes, announce } = args
  const addToast = useToastStore((s) => s.add)

  const [dragOverDeptId, setDragOverDeptId] = useState<string | null>(null)
  const dragOverDeptIdRef = useRef<string | null>(null)
  const dragOriginalDeptRef = useRef<string | null>(null)

  const deptBounds = useMemo<DepartmentBounds[]>(() => {
    return displayNodes
      .filter((n) => n.type === 'department')
      .map((n) => ({
        departmentName: (n.data as import('./build-org-tree').DepartmentGroupData).departmentName,
        nodeId: n.id,
        x: n.position.x,
        y: n.position.y,
        width: (n.measured?.width ?? n.width ?? 200) as number,
        height: (n.measured?.height ?? n.height ?? 120) as number,
      }))
  }, [displayNodes])

  const handleNodeDragStart = useCallback(
    (_event: ReactMouseEvent, node: Node) => {
      if (node.type !== 'agent') return
      if (viewMode !== 'hierarchy') return
      const dept = (node.data as AgentNodeData).department
      dragOriginalDeptRef.current = dept
      const name = (node.data as AgentNodeData).name
      announce(`Started dragging ${name}`)
    },
    [viewMode, announce],
  )

  const handleNodeDrag = useCallback(
    (_event: ReactMouseEvent, node: Node) => {
      if (!dragOriginalDeptRef.current) return
      const centerX = node.position.x + ((node.measured?.width ?? AGENT_NODE_WIDTH) / 2)
      const centerY = node.position.y + ((node.measured?.height ?? AGENT_NODE_HEIGHT) / 2)
      const target = findDropTarget({ x: centerX, y: centerY }, deptBounds)
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
    (_event: ReactMouseEvent, node: Node) => {
      const originalDept = dragOriginalDeptRef.current
      dragOriginalDeptRef.current = null
      dragOverDeptIdRef.current = null
      setDragOverDeptId(null)

      if (!originalDept) return
      if (node.type !== 'agent') return

      const centerX = node.position.x + ((node.measured?.width ?? AGENT_NODE_WIDTH) / 2)
      const centerY = node.position.y + ((node.measured?.height ?? AGENT_NODE_HEIGHT) / 2)
      const target = findDropTarget({ x: centerX, y: centerY }, deptBounds)

      const agentName = (node.data as AgentNodeData).name
      const newDept = target?.departmentName

      if (!newDept || newDept === originalDept) {
        announce(`Cancelled moving ${agentName}`)
        return
      }

      if (!isDepartmentName(newDept)) {
        announce(`Failed to move ${agentName}`)
        addToast({ variant: 'error', title: 'Reassignment failed', description: 'Invalid department target' })
        return
      }

      const newDeptName: DepartmentName = newDept
      const rollback = useCompanyStore.getState().optimisticReassignAgent(agentName, newDeptName)

      useCompanyStore.getState().updateAgent(agentName, { department: newDeptName, autonomy_level: null, level: null })
        .then(() => {
          announce(`Moved ${agentName} to ${newDept}`)
          addToast({ variant: 'success', title: `Moved ${agentName} to ${newDept}` })
        })
        .catch((err: unknown) => {
          rollback()
          const msg = err instanceof Error ? err.message : 'Unknown error'
          const currentDept = useCompanyStore.getState().config?.agents.find((a) => a.name === agentName)?.department
          if (currentDept === originalDept) {
            announce(`Failed to move ${agentName}, returned to ${originalDept}`)
          } else {
            announce(`Failed to move ${agentName}`)
          }
          addToast({ variant: 'error', title: 'Reassignment failed', description: msg })
        })
    },
    [deptBounds, addToast, announce],
  )

  return { dragOverDeptId, handleNodeDragStart, handleNodeDrag, handleNodeDragStop }
}
