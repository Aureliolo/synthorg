import { useMemo } from 'react'
import { ReactFlow, Background, Controls, type Node, type Edge, MarkerType, Position } from '@xyflow/react'
// @xyflow CSS is imported globally in styles/global.css
import { getTaskStatusColor, getTaskStatusLabel } from '@/utils/tasks'
import type { Task } from '@/api/types/tasks'

export interface TaskDependencyGraphProps {
  tasks: Task[]
  onSelectTask: (taskId: string) => void
  height?: string | number
}

function buildGraph(tasks: Task[]): { nodes: Node[]; edges: Edge[] } {
  const taskMap = new Map(tasks.map((t) => [t.id, t]))
  const nodes: Node[] = []
  const edges: Edge[] = []
  const COL_WIDTH = 280
  const ROW_HEIGHT = 80

  // Only include tasks that have dependencies or are depended upon
  const relevantIds = new Set<string>()
  for (const task of tasks) {
    if (task.dependencies.length > 0) {
      relevantIds.add(task.id)
      for (const depId of task.dependencies) {
        relevantIds.add(depId)
      }
    }
  }

  const relevantTasks = tasks.filter((t) => relevantIds.has(t.id))
  if (relevantTasks.length === 0) return { nodes: [], edges: [] }

  // Arrange in a simple grid
  relevantTasks.forEach((task, i) => {
    const col = i % 4
    const row = Math.floor(i / 4)
    const color = getTaskStatusColor(task.status)
    const cssColor = color === 'text-secondary' ? 'var(--so-text-secondary)' : `var(--so-${color})`

    nodes.push({
      id: task.id,
      position: { x: col * COL_WIDTH, y: row * ROW_HEIGHT },
      data: { label: task.title },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      style: {
        borderColor: cssColor,
        borderWidth: 2,
        borderRadius: 'var(--so-radius-lg)',
        padding: 'var(--so-space-2) var(--so-space-3)',
        fontSize: 'var(--so-text-compact)',
        maxWidth: 240,
        background: 'var(--so-card)',
        color: 'var(--so-foreground)',
      },
    })
  })

  // Build edges from dependencies
  for (const task of relevantTasks) {
    for (const depId of task.dependencies) {
      const depTask = taskMap.get(depId)
      if (!depTask) continue
      edges.push({
        id: `${depId}->${task.id}`,
        source: depId,
        target: task.id,
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        style: { stroke: 'var(--so-border-bright)', strokeWidth: 'var(--so-stroke-thin)' },
        label: getTaskStatusLabel(depTask.status),
        labelStyle: { fontSize: 'var(--so-text-micro)', fill: 'var(--so-text-muted)' },
      })
    }
  }

  return { nodes, edges }
}

export function TaskDependencyGraph({ tasks, onSelectTask, height = 400 }: TaskDependencyGraphProps) {
  const { nodes, edges } = useMemo(() => buildGraph(tasks), [tasks])

  if (nodes.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-surface px-4 py-8 text-center text-sm text-text-muted">
        No task dependencies to visualize.
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-border bg-surface" style={{ height: typeof height === 'number' ? `${height}px` : height }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodeClick={(_event, node) => onSelectTask(node.id)}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        proOptions={{ hideAttribution: true }}
        minZoom={0.3}
        maxZoom={1.5}
      >
        <Background gap={20} size={1} color="var(--so-border)" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}
