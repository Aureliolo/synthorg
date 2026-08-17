import { ReactFlowProvider, type Edge, type Node } from '@xyflow/react'
import { GitBranch, Loader2, MessagesSquare } from 'lucide-react'
import { Link } from 'react-router'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { EmptyState } from '@/components/ui/empty-state'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { WsConnectionBanner } from '@/components/ui/ws-connection-banner'
import { OrgChartToolbar } from './org/OrgChartToolbar'
import { OrgChartSkeleton } from './org/OrgChartSkeleton'
import { OrgChartBanners } from './org/OrgChartBanners'
import { OrgChartCanvas } from './org/OrgChartCanvas'
import { useOrgChartController } from './org/useOrgChartController'
import { ROUTES } from '@/router/routes'

interface TransitionIndicatorProps {
  commLoading: boolean
  transitioning: boolean
  viewMode: 'hierarchy' | 'force'
}

/** Spinner shown while communication data loads or a view transitions. */
function OrgChartTransitionIndicator({ commLoading, transitioning, viewMode }: TransitionIndicatorProps) {
  if (!((commLoading || transitioning) && viewMode === 'force')) return null
  return (
    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
      {commLoading ? 'Loading communication data...' : 'Transitioning...'}
    </div>
  )
}

// Stable identities so the empty branch never feeds React Flow a fresh
// array each render.
const EMPTY_NODES: Node[] = []
const EMPTY_EDGES: Edge[] = []

/** Communication view has meaning only once agents exchange messages;
 * without any links the force layout is just a scattered node cloud, so we
 * show an explanatory empty state on a clean canvas instead. */
function deriveCommunicationEmpty(ctrl: ReturnType<typeof useOrgChartController>): boolean {
  const isForce = ctrl.viewMode === 'force'
  const hasCommEdges = ctrl.renderedEdges.some((e) => e.type === 'communication')
  return isForce && !ctrl.data.commLoading && !ctrl.view.transitioning && !hasCommEdges
}

function CommunicationEmptyState() {
  return (
    <EmptyState
      icon={MessagesSquare}
      title="No communication activity yet"
      description="This view maps message volume between agents: who talks to whom, with busier pairs drawn closer together. Once your agents start collaborating, the graph fills in here."
    />
  )
}

function OrgChartInner() {
  const ctrl = useOrgChartController()
  const { data } = ctrl

  if (data.loading && data.nodes.length === 0) {
    return <OrgChartSkeleton />
  }

  if (!data.loading && data.nodes.length === 0 && !data.error) {
    return (
      <EmptyState
        icon={GitBranch}
        title="No organisation configured"
        description="Set up your company and agents to see the org chart."
        action={{ label: 'Edit Organisation', onClick: ctrl.goToOrgEdit }}
      />
    )
  }

  return <OrgChartBody ctrl={ctrl} />
}

function OrgChartBody({ ctrl }: { ctrl: ReturnType<typeof useOrgChartController> }) {
  const { data } = ctrl
  const communicationEmpty = deriveCommunicationEmpty(ctrl)
  return (
    <div className="flex h-full flex-col">
      <OrgChartBanners
        error={data.error}
        commError={data.commError}
        commTruncated={data.commTruncated}
        onRetryComm={data.refetchComm}
        wsConnected={data.wsConnected}
        wsSetupError={data.wsSetupError}
      />

      <div className="flex items-center justify-between pb-3">
        <OrgChartToolbar
          viewMode={ctrl.viewMode}
          onViewModeChange={ctrl.handleViewModeChange}
          onFitView={ctrl.onFitView}
          onZoomIn={ctrl.onZoomIn}
          onZoomOut={ctrl.onZoomOut}
          onExportPng={ctrl.png.handleExportPng}
          exporting={ctrl.png.exporting}
          onPrint={ctrl.png.handlePrint}
        />
        <OrgChartTransitionIndicator
          commLoading={data.commLoading}
          transitioning={ctrl.view.transitioning}
          viewMode={ctrl.viewMode}
        />
      </div>

      <OrgChartCanvas
        flowWrapperRef={ctrl.png.flowWrapperRef}
        renderedNodes={communicationEmpty ? EMPTY_NODES : ctrl.renderedNodes}
        renderedEdges={communicationEmpty ? EMPTY_EDGES : ctrl.renderedEdges}
        selection={ctrl.selection}
        onEdgeMouseEnter={ctrl.edge.onEdgeMouseEnter}
        onEdgeMouseLeave={ctrl.edge.onEdgeMouseLeave}
        onEdgeClick={ctrl.edge.onEdgeClick}
        handleNodeDragStart={ctrl.drag.handleNodeDragStart}
        handleNodeDrag={ctrl.drag.handleNodeDrag}
        handleNodeDragStop={ctrl.drag.handleNodeDragStop}
        dragEnabled={ctrl.dragEnabled}
        showMinimap={ctrl.showMinimap}
        filterOverlay={ctrl.filterOverlay}
        emptyOverlay={communicationEmpty ? <CommunicationEmptyState /> : null}
        announcement={ctrl.announcement}
      />

      <ConfirmDialog
        open={ctrl.selection.deleteConfirm !== null}
        onOpenChange={(open) => {
          if (!open) ctrl.selection.setDeleteConfirm(null)
        }}
        title={`Delete "${ctrl.selection.deleteConfirm?.label}"?`}
        description="This action cannot be undone."
        variant="destructive"
        confirmLabel="Delete"
        onConfirm={ctrl.selection.confirmDelete}
      />
    </div>
  )
}

export default function OrgChartPage() {
  return (
    <div className="flex h-full flex-col gap-section-gap">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-foreground">Org Chart</h1>
        <Button asChild variant="outline" size="sm">
          <Link to={ROUTES.ORG_EDIT}>Edit Organisation</Link>
        </Button>
      </div>

      <WsConnectionBanner description="Live edge activity may be stale until the connection recovers." />

      <ErrorBoundary level="section">
        <ReactFlowProvider>
          <OrgChartInner />
        </ReactFlowProvider>
      </ErrorBoundary>
    </div>
  )
}
