import {
  Background,
  MiniMap,
  ReactFlow,
  type Edge,
  type EdgeMouseHandler,
  type Node,
  type OnNodeDrag,
} from '@xyflow/react'
import { LiveRegion } from '@/components/ui/live-region'
import { AgentNode } from './AgentNode'
import { CeoNode } from './CeoNode'
import { DepartmentGroupNode } from './DepartmentGroupNode'
import { TeamGroupNode } from './TeamGroupNode'
import { OwnerNode } from './OwnerNode'
import { DeptAdminNode } from './DeptAdminNode'
import { HierarchyEdge } from './HierarchyEdge'
import { CommunicationEdge } from './CommunicationEdge'
import { NodeContextMenu } from './NodeContextMenu'
import type { OrgChartSelectionResult } from './useOrgChartSelection'

const nodeTypes = {
  agent: AgentNode,
  ceo: CeoNode,
  department: DepartmentGroupNode,
  team: TeamGroupNode,
  owner: OwnerNode,
  deptAdmin: DeptAdminNode,
}
const edgeTypes = { hierarchy: HierarchyEdge, communication: CommunicationEdge }

// xyflow MiniMap props are typed as `number` and reject CSS vars;
// numeric constants with a comment pointing to the corresponding design
// token prevent theme drift (see web/CLAUDE.md Design Token Rules).
const MINIMAP_STROKE_WIDTH = 1.5 // var(--so-stroke-thin)
const MINIMAP_NODE_BORDER_RADIUS = 4 // var(--so-radius-sm)

function miniMapNodeColor(n: Node): string {
  if (n.type === 'owner') return 'var(--so-minimap-node-owner)'
  if (n.type === 'department') return 'var(--so-minimap-node-dept)'
  return 'var(--so-minimap-node-agent)'
}

function miniMapNodeStroke(n: Node): string {
  return n.type === 'department' ? 'var(--so-minimap-stroke)' : 'transparent'
}

function OrgChartMiniMap() {
  return (
    <MiniMap
      pannable
      zoomable
      ariaLabel="Org chart minimap"
      position="bottom-right"
      bgColor="var(--so-minimap-bg)"
      maskColor="var(--so-minimap-mask)"
      maskStrokeColor="var(--so-minimap-stroke)"
      maskStrokeWidth={MINIMAP_STROKE_WIDTH}
      style={{
        width: 260,
        height: 200,
        resize: 'both',
        overflow: 'hidden',
        border: '1px solid var(--so-minimap-border)',
        borderRadius: 'var(--so-radius-xl)',
        boxShadow: 'var(--so-minimap-shadow)',
      }}
      nodeColor={miniMapNodeColor}
      nodeStrokeColor={miniMapNodeStroke}
      nodeStrokeWidth={MINIMAP_STROKE_WIDTH}
      nodeBorderRadius={MINIMAP_NODE_BORDER_RADIUS}
    />
  )
}

export interface OrgChartCanvasProps {
  flowWrapperRef: React.RefObject<HTMLDivElement | null>
  renderedNodes: Node[]
  renderedEdges: Edge[]
  onMoveEnd: (event: unknown, viewport: { x: number; y: number; zoom: number }) => void
  selection: OrgChartSelectionResult
  onEdgeMouseEnter: EdgeMouseHandler
  onEdgeMouseLeave: EdgeMouseHandler
  onEdgeClick: EdgeMouseHandler
  handleNodeDragStart: OnNodeDrag<Node>
  handleNodeDrag: OnNodeDrag<Node>
  handleNodeDragStop: OnNodeDrag<Node>
  dragEnabled: boolean
  showMinimap: boolean
  filterOverlay: React.ReactNode
  announcement: { id: number; text: string } | null
}

/** The ReactFlow canvas plus its overlays, live region, and menu. */
export function OrgChartCanvas({
  flowWrapperRef,
  renderedNodes,
  renderedEdges,
  onMoveEnd,
  selection,
  onEdgeMouseEnter,
  onEdgeMouseLeave,
  onEdgeClick,
  handleNodeDragStart,
  handleNodeDrag,
  handleNodeDragStop,
  dragEnabled,
  showMinimap,
  filterOverlay,
  announcement,
}: OrgChartCanvasProps) {
  return (
    <div
      ref={flowWrapperRef}
      className="relative flex-1 rounded-lg border border-border print:border-0"
      data-testid="org-chart-canvas"
    >
      <ReactFlow
        aria-label="Organization chart"
        nodes={renderedNodes}
        edges={renderedEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        onMoveEnd={onMoveEnd}
        onNodeClick={selection.handleNodeClick}
        onNodeContextMenu={selection.handleNodeContextMenu}
        onEdgeMouseEnter={onEdgeMouseEnter}
        onEdgeMouseLeave={onEdgeMouseLeave}
        onEdgeClick={onEdgeClick}
        onNodeDragStart={dragEnabled ? handleNodeDragStart : undefined}
        onNodeDrag={dragEnabled ? handleNodeDrag : undefined}
        onNodeDragStop={dragEnabled ? handleNodeDragStop : undefined}
        onPaneClick={selection.handlePaneClick}
        nodesConnectable={false}
        minZoom={0.1}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="var(--color-border)" gap={24} size={1} />
        {showMinimap && <OrgChartMiniMap />}
      </ReactFlow>

      {filterOverlay}

      <LiveRegion politeness="assertive" className="sr-only">
        {announcement ? <span key={announcement.id}>{announcement.text}</span> : null}
      </LiveRegion>

      {selection.contextMenu && (
        <NodeContextMenu
          nodeId={selection.contextMenu.nodeId}
          nodeType={selection.contextMenu.nodeType}
          position={selection.contextMenu.position}
          onClose={() => selection.setContextMenu(null)}
          onViewDetails={selection.handleViewDetails}
          onDelete={selection.handleDelete}
        />
      )}
    </div>
  )
}
