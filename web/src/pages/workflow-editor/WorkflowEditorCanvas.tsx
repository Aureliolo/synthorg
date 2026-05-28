import type { MouseEvent as ReactMouseEvent } from 'react'
import {
  Background,
  MiniMap,
  ReactFlow,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type Connection,
  type EdgeTypes,
  type NodeTypes,
} from '@xyflow/react'

interface WorkflowEditorCanvasProps {
  nodes: readonly Node[]
  edges: readonly Edge[]
  nodeTypes: NodeTypes
  edgeTypes: EdgeTypes
  defaultViewport: { x: number; y: number; zoom: number } | undefined
  onNodeClick: (event: ReactMouseEvent, node: Node) => void
  onPaneClick: () => void
  onConnect: (connection: Connection) => void
  onNodesChange: (changes: NodeChange[]) => void
  onEdgesChange: (changes: EdgeChange[]) => void
  onMoveEnd: (event: unknown, viewport: { x: number; y: number; zoom: number }) => void
}

const MINI_MAP_COLOR_MAP: Readonly<Record<string, string>> = {
  start: 'var(--so-accent)',
  end: 'var(--so-accent)',
  task: 'var(--so-accent)',
  conditional: 'var(--so-warning)',
  parallel_split: 'var(--so-success)',
  parallel_join: 'var(--so-success)',
  agent_assignment: 'var(--so-accent-dim)',
}

function miniMapNodeColor(node: Node): string {
  if (!node.type) return 'var(--so-text-muted)'
  return MINI_MAP_COLOR_MAP[node.type] ?? 'var(--so-text-muted)'
}

export function WorkflowEditorCanvas(props: WorkflowEditorCanvasProps) {
  const { nodes, edges } = props
  return (
    <div className="relative min-h-0 flex-1 rounded-lg border border-border">
      <GraphSummary nodes={nodes} edges={edges} />
      <FlowCanvas {...props} />
    </div>
  )
}

interface GraphSummaryProps {
  nodes: readonly Node[]
  edges: readonly Edge[]
}

function GraphSummary({ nodes, edges }: GraphSummaryProps) {
  // ReactFlow's visual canvas is mouse-first; screen-reader users get this
  // sr-only summary, referenced via aria-describedby on the canvas.
  return (
    <section
      id="workflow-editor-node-summary"
      aria-labelledby="workflow-editor-node-summary-heading"
      className="sr-only"
    >
      <h2 id="workflow-editor-node-summary-heading">Workflow graph summary</h2>
      <h3 id="workflow-editor-node-summary-nodes">Nodes ({nodes.length})</h3>
      <ul aria-labelledby="workflow-editor-node-summary-nodes">
        {nodes.map((node) => (
          <li key={node.id}>
            {`Node ${node.id} (${node.type ?? 'unknown'}): ${extractNodeLabel(node)}`}
          </li>
        ))}
      </ul>
      <h3 id="workflow-editor-node-summary-edges">Edges ({edges.length})</h3>
      <ul aria-labelledby="workflow-editor-node-summary-edges">
        {edges.map((edge) => (
          <li key={edge.id}>{`Edge: ${describeEdge(edge)}`}</li>
        ))}
      </ul>
    </section>
  )
}

function extractNodeLabel(node: Node): string {
  if (node.data && typeof node.data === 'object' && 'label' in node.data) {
    const label = (node.data as { label?: unknown }).label
    if (label != null && label !== '') return String(label)
  }
  return node.type ?? node.id
}

function describeEdge(edge: Edge): string {
  const topology = `${edge.source} → ${edge.target}`
  const rawLabel = extractEdgeLabel(edge)
  return rawLabel ? `${topology} (${rawLabel})` : topology
}

function extractEdgeLabel(edge: Edge): string {
  if (typeof edge.label === 'string' && edge.label) return edge.label
  if (
    edge.data &&
    typeof edge.data === 'object' &&
    'label' in edge.data &&
    typeof (edge.data as { label?: unknown }).label === 'string'
  ) {
    return (edge.data as { label: string }).label
  }
  return ''
}

function FlowCanvas(props: WorkflowEditorCanvasProps) {
  const {
    nodes,
    edges,
    nodeTypes,
    edgeTypes,
    defaultViewport,
    onNodeClick,
    onPaneClick,
    onConnect,
    onNodesChange,
    onEdgesChange,
    onMoveEnd,
  } = props
  return (
    <ReactFlow
      aria-label="Workflow editor canvas"
      aria-describedby="workflow-editor-node-summary"
      nodes={nodes as Node[]}
      edges={edges as Edge[]}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      defaultViewport={defaultViewport}
      fitView={!defaultViewport}
      fitViewOptions={{ padding: 0.2 }}
      onMoveEnd={onMoveEnd}
      onNodeClick={onNodeClick}
      onPaneClick={onPaneClick}
      onConnect={onConnect}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      selectionOnDrag
      minZoom={0.1}
      maxZoom={2}
      proOptions={{ hideAttribution: true }}
    >
      <Background color="var(--color-border)" gap={24} size={1} />
      <MiniMap
        position="bottom-right"
        pannable
        zoomable
        style={{ backgroundColor: 'var(--so-bg-surface)' }}
        maskColor="var(--so-bg-overlay)"
        nodeColor={miniMapNodeColor}
      />
    </ReactFlow>
  )
}
