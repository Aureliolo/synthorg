import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react'
import { toPng } from 'html-to-image'
import { GitBranch, Loader2 } from 'lucide-react'
import { ErrorBanner } from '@/components/ui/error-banner'
import { Link, useNavigate } from 'react-router'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { sanitizeForLog } from '@/utils/logging'

/**
 * Produce a filesystem-safe `YYYY-MM-DD` stamp for download filenames.
 * `formatDateOnly` is locale-aware and can emit slashes / dots /
 * non-ASCII digits (e.g. `ar-EG`), which shell + HTTP clients treat
 * poorly in filenames. The PNG export only needs a stable, sortable
 * UTC day stamp.
 */
function isoDateStamp(d: Date): string {
  return d.toISOString().slice(0, 10)
}

/**
 * True when a computed background-color string has a visible fill. Both
 * `transparent` and `rgba(...,0)` resolve to zero-alpha rgba strings in
 * `getComputedStyle`; treat any missing / zero-alpha value as
 * transparent so the PNG exporter falls through to the next source.
 */
function isOpaque(color: string | undefined | null): color is string {
  if (!color) return false
  const trimmed = color.trim()
  if (!trimmed || trimmed === 'transparent') return false
  // Extract the alpha channel from `rgba(r, g, b, a)` without a full
  // regex parse to keep the check simple and avoid ReDoS territory.
  const openParen = trimmed.indexOf('(')
  const closeParen = trimmed.lastIndexOf(')')
  if (openParen === -1 || closeParen === -1) return true
  const parts = trimmed.slice(openParen + 1, closeParen).split(',').map((p) => p.trim())
  if (parts.length !== 4) return true
  const alpha = Number.parseFloat(parts[3] ?? '1')
  return Number.isFinite(alpha) && alpha > 0
}
import { useOrgChartData } from '@/hooks/useOrgChartData'
import { useRegisterCommands } from '@/hooks/useCommandPalette'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { EmptyState } from '@/components/ui/empty-state'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { LiveRegion } from '@/components/ui/live-region'
import { useOrgChartPrefs } from '@/stores/org-chart-prefs'
import { useLiveEdgeActivity } from '@/hooks/useLiveEdgeActivity'
import { AgentNode } from './org/AgentNode'
import { CeoNode } from './org/CeoNode'
import { DepartmentGroupNode } from './org/DepartmentGroupNode'
import { TeamGroupNode } from './org/TeamGroupNode'
import { OwnerNode } from './org/OwnerNode'
import { DeptAdminNode } from './org/DeptAdminNode'
import { HierarchyEdge } from './org/HierarchyEdge'
import { CommunicationEdge } from './org/CommunicationEdge'
import { OrgChartToolbar } from './org/OrgChartToolbar'
import { OrgChartSkeleton } from './org/OrgChartSkeleton'
import { NodeContextMenu } from './org/NodeContextMenu'
import { useOrgChartDragDrop } from './org/useOrgChartDragDrop'
import { useOrgChartEdgeInteraction } from './org/OrgChartEdgeInteraction'
import { useOrgChartFilter } from './org/OrgChartFilter'
import { useOrgChartSelection } from './org/useOrgChartSelection'
import { useOrgChartViewMode } from './org/useOrgChartViewMode'
import { ROUTES } from '@/router/routes'

const log = createLogger('OrgChart')

const nodeTypes = {
  agent: AgentNode,
  ceo: CeoNode,
  department: DepartmentGroupNode,
  team: TeamGroupNode,
  owner: OwnerNode,
  deptAdmin: DeptAdminNode,
}
const edgeTypes = { hierarchy: HierarchyEdge, communication: CommunicationEdge }

const VIEWPORT_KEY = 'synthorg:orgchart:viewport'
const COLLAPSED_DEPTS_KEY = 'synthorg:orgchart:collapsed-depts'

// xyflow MiniMap props are typed as `number` and reject CSS vars;
// numeric constants with a comment pointing to the corresponding design
// token prevent theme drift (see web/CLAUDE.md Design Token Rules).
const MINIMAP_STROKE_WIDTH = 1.5 // var(--so-stroke-thin)
const MINIMAP_NODE_BORDER_RADIUS = 4 // var(--so-radius-sm)

interface ViewportState {
  x: number
  y: number
  zoom: number
}

/** Shared edge-data shape for the org chart. Narrower than xyflow's default
 *  `Record<string, unknown>` so edge data merges stay type-safe. */
interface OrgChartEdgeData extends Record<string, unknown> {
  particlesVisible?: boolean
  hovered?: boolean
}

function saveViewport(viewport: ViewportState) {
  try {
    localStorage.setItem(VIEWPORT_KEY, JSON.stringify(viewport))
  } catch (err) {
    log.warn('Failed to save viewport:', err)
  }
}

function loadCollapsedDepts(): Set<string> {
  try {
    const stored = localStorage.getItem(COLLAPSED_DEPTS_KEY)
    if (!stored) return new Set()
    const parsed: unknown = JSON.parse(stored)
    if (!Array.isArray(parsed) || !parsed.every((entry): entry is string => typeof entry === 'string')) {
      log.warn('Discarding malformed collapsed-depts storage payload', { type: typeof parsed })
      return new Set()
    }
    return new Set<string>(parsed)
  } catch (err) {
    log.warn('Failed to load collapsed depts from localStorage:', err)
  }
  return new Set()
}

function OrgChartInner() {
  const [collapsedDepts, setCollapsedDepts] = useState<Set<string>>(loadCollapsedDepts)

  const toggleDeptCollapsed = useCallback((deptId: string) => {
    setCollapsedDepts((prev) => {
      const next = new Set(prev)
      if (next.has(deptId)) next.delete(deptId)
      else next.add(deptId)
      try {
        localStorage.setItem(COLLAPSED_DEPTS_KEY, JSON.stringify([...next]))
      } catch (err) {
        log.warn('Failed to persist collapsed depts:', err)
      }
      return next
    })
  }, [])

  // Drag/drop and reassignment hooks call `announce(text)` to push messages
  // to the shared `<LiveRegion>`. The state carries an incrementing `id`
  // alongside the text so identical consecutive messages still re-fire the
  // aria-live region (React skips re-renders when a bare string state is
  // set to the same value; a fresh object + keyed span inside LiveRegion
  // force a DOM swap that screen readers announce).
  const [announcement, setAnnouncement] = useState<{ id: number; text: string } | null>(null)
  const announce = useCallback((text: string) => {
    setAnnouncement((prev) => ({ id: (prev?.id ?? 0) + 1, text }))
  }, [])

  const { fitView, zoomIn, zoomOut } = useReactFlow()
  const navigate = useNavigate()

  const particleFlowMode = useOrgChartPrefs((s) => s.particleFlowMode)
  const showMinimap = useOrgChartPrefs((s) => s.showMinimap)

  // Page owns viewMode as the single source of truth: it is read by
  // `useOrgChartData` before the fetch and re-read by `useOrgChartViewMode`
  // to drive the transition animation.
  const [viewMode, setViewMode] = useState<'hierarchy' | 'force'>('hierarchy')

  const {
    nodes,
    edges,
    allNodes,
    loading,
    error,
    commLoading,
    commError,
    commTruncated,
    wsConnected,
    wsSetupError,
  } = useOrgChartData(viewMode, collapsedDepts)

  const view = useOrgChartViewMode(nodes, edges, viewMode)
  // Page owns viewMode as the single source of truth: useOrgChartData
  // reads it before fetch, and useOrgChartViewMode re-animates when it
  // changes (via the effect's nodes/edges dependency list).
  const handleViewModeChange = useCallback((mode: 'hierarchy' | 'force') => {
    setViewMode(mode)
  }, [])

  // Re-fit the viewport whenever the view mode flips OR the layout
  // animation finishes. The hierarchy layout produces coordinates in the
  // thousands of pixels; the force layout centres around (~400, ~300).
  // Without a re-fit, the saved viewport from the previous layout makes
  // nodes appear scattered far apart or vanishingly small in the corner.
  // We wait for ``transitioning`` to flip back to false so we fit the
  // FINAL positions, not the in-flight interpolated ones.
  useEffect(() => {
    if (view.transitioning) return
    if (view.displayNodes.length === 0) return
    // ``requestAnimationFrame`` lets ReactFlow commit the new node
    // positions to its internal store before we ask it to fit them.
    const id = requestAnimationFrame(() => {
      fitView({ padding: 0.2, duration: 400 })
    })
    return () => cancelAnimationFrame(id)
  }, [viewMode, view.transitioning, view.displayNodes.length, fitView])

  // Pre-render source list: transition reducer publishes `displayNodes` only
  // after the first animation frame, so before any layout change settles we
  // fall back to the raw `nodes` array. The drag/selection hooks and the
  // rendered node list both read from this single source so a drag hit-test
  // and the visible node it targets always refer to the same positions.
  const sourceNodes = view.displayNodes.length > 0 ? view.displayNodes : nodes

  const { dragOverDeptId, handleNodeDragStart, handleNodeDrag, handleNodeDragStop } =
    useOrgChartDragDrop({ viewMode, displayNodes: sourceNodes, announce })

  const selection = useOrgChartSelection(sourceNodes)
  const filter = useOrgChartFilter(allNodes)

  const edgeIdByAgentPair = useMemo(() => {
    const map = new Map<string, string>()
    if (particleFlowMode !== 'live') return map
    for (const edge of edges) {
      if (edge.hidden) continue
      map.set(`${edge.source}::${edge.target}`, edge.id)
    }
    return map
  }, [edges, particleFlowMode])

  const liveActiveEdgeIds = useLiveEdgeActivity(edgeIdByAgentPair)

  const orgChartCommands = useMemo(
    () => [
      {
        id: 'org-fit-view',
        label: 'Fit to View',
        description: 'Reset zoom to fit all nodes',
        icon: GitBranch,
        action: () => fitView({ padding: 0.2 }),
        group: 'Org Chart',
        scope: 'local' as const,
      },
    ],
    [fitView],
  )
  useRegisterCommands(orgChartCommands)

  const handleMoveEnd = useCallback((_event: unknown, viewport: ViewportState) => {
    saveViewport(viewport)
  }, [])

  // Ref to the ReactFlow wrapper so html-to-image can snapshot the chart.
  // Using the class selector fallback as a defence-in-depth because the
  // wrapper receives the `.react-flow` class from ReactFlow itself.
  const flowWrapperRef = useRef<HTMLDivElement | null>(null)
  const [exporting, setExporting] = useState(false)

  const handleExportPng = useCallback(async () => {
    const target =
      flowWrapperRef.current?.querySelector<HTMLElement>('.react-flow') ??
      flowWrapperRef.current
    if (!target) return
    // Fit before snapshot so the PNG captures the full graph rather than
    // whatever pan/zoom the user happens to have.
    void fitView({ padding: 0.2, duration: 0 })
    setExporting(true)
    let dataUrl: string | null
    try {
      // Wait for the browser to commit the fitView transform before the
      // snapshot. A single requestAnimationFrame is enough because
      // `duration: 0` disables ReactFlow's transition.
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve())
      })
      // Resolve the live background from `--so-background`; fall back to
      // `--so-surface`, then to the computed body background, then to the
      // chart element itself. A transparent PNG would render on top of
      // whatever the target viewer shows, which is almost never what we
      // want. NO hardcoded colors -- the design system forbids them.
      const rootStyle = getComputedStyle(document.documentElement)
      const tokenBackground =
        rootStyle.getPropertyValue('--so-background').trim() ||
        rootStyle.getPropertyValue('--so-surface').trim()
      const bodyBackground = getComputedStyle(document.body).backgroundColor
      const targetBackground = getComputedStyle(target).backgroundColor
      const backgroundColor =
        tokenBackground ||
        (isOpaque(bodyBackground) ? bodyBackground : undefined) ||
        (isOpaque(targetBackground) ? targetBackground : undefined)
      dataUrl = await toPng(target, {
        backgroundColor,
        pixelRatio: 2,
        cacheBust: true,
      })
      const link = document.createElement('a')
      link.href = dataUrl
      link.download = `org-chart-${isoDateStamp(new Date())}.png`
      link.click()
    } catch (err) {
      log.error('Org chart PNG export failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Export failed',
        description:
          'Could not render the chart to PNG. Try again, or use Print for a fallback.',
      })
      return
    } finally {
      setExporting(false)
    }
    // Success toast is outside the try so a downstream toast-store error
    // does not get misreported as a PNG export failure. `dataUrl` is
    // guaranteed non-null here because the catch branch returns early.
    if (dataUrl) {
      useToastStore.getState().add({
        variant: 'success',
        title: 'Org chart exported',
      })
    }
  }, [fitView])

  const handlePrint = useCallback(() => {
    // Fit-to-view before print so the user sees the full chart in the
    // print preview rather than whatever viewport they had zoomed to.
    void fitView({ padding: 0.2, duration: 0 })
    // fitView runs instantly (duration: 0) -- the single-frame delay is
    // to let the browser commit the resulting layout/repaint before
    // window.print() freezes the page.
    requestAnimationFrame(() => {
      window.print()
    })
  }, [fitView])

  const renderedNodes = useMemo(() => {
    return sourceNodes.map((n) => {
      const isDropTarget = dragOverDeptId !== null && n.type === 'department' && n.id === dragOverDeptId
      const isDeptNode = n.type === 'department'
      const dimmed = filter.highlightedNodeIds !== null && !filter.highlightedNodeIds.has(n.id)
      const next = { ...n }
      next.draggable = viewMode === 'hierarchy' && n.type === 'agent'
      if (isDeptNode) {
        next.data = { ...n.data, onToggleCollapsed: toggleDeptCollapsed }
      }
      if (isDropTarget) {
        next.data = { ...next.data, isDropTarget: true }
      }
      if (dimmed) {
        next.style = { ...n.style, opacity: 0.25, transition: `opacity var(--so-transition-dim) ease` }
      } else if (n.style && typeof (n.style as { opacity?: number }).opacity === 'number') {
        const rest = { ...n.style } as Record<string, unknown>
        delete rest['opacity']
        next.style = rest
      }
      return next
    })
  }, [sourceNodes, dragOverDeptId, filter.highlightedNodeIds, toggleDeptCollapsed, viewMode])

  const edgesWithParticles = useMemo<Edge<OrgChartEdgeData>[]>(() => {
    return view.displayEdges.map((e) => {
      const particlesVisible =
        particleFlowMode === 'always'
          ? true
          : particleFlowMode === 'live'
            ? liveActiveEdgeIds.has(e.id)
            : false
      const existing = (e.data ?? {}) as OrgChartEdgeData
      return { ...e, data: { ...existing, particlesVisible } }
    })
  }, [view.displayEdges, particleFlowMode, liveActiveEdgeIds])

  const {
    edgesWithHoverState: renderedEdges,
    onEdgeMouseEnter,
    onEdgeMouseLeave,
    onEdgeClick,
  } = useOrgChartEdgeInteraction<OrgChartEdgeData>({ edges: edgesWithParticles })

  if (loading && nodes.length === 0) {
    return <OrgChartSkeleton />
  }

  if (!loading && nodes.length === 0 && !error) {
    return (
      <EmptyState
        icon={GitBranch}
        title="No organization configured"
        description="Set up your company and agents to see the org chart"
        action={{ label: 'Edit Organization', onClick: () => { void navigate(ROUTES.ORG_EDIT) } }}
      />
    )
  }

  return (
    <div className="flex h-full flex-col">
      {error && (
        <ErrorBanner severity="error" title="Could not load org chart" description={error} />
      )}
      {commError && (
        <ErrorBanner
          variant="inline"
          severity="warning"
          title="Communication data unavailable"
          description={commError}
        />
      )}
      {commTruncated && !commError && (
        <ErrorBanner
          variant="inline"
          severity="info"
          title="Partial communication graph"
          description="Message limit reached; showing available data."
        />
      )}
      {!wsConnected && wsSetupError && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates unavailable"
          description={wsSetupError}
        />
      )}

      <div className="flex items-center justify-between pb-3">
        <OrgChartToolbar
          viewMode={viewMode}
          onViewModeChange={handleViewModeChange}
          onFitView={() => fitView({ padding: 0.2 })}
          onZoomIn={() => zoomIn()}
          onZoomOut={() => zoomOut()}
          onExportPng={handleExportPng}
          exporting={exporting}
          onPrint={handlePrint}
        />
        {(commLoading || view.transitioning) && viewMode === 'force' && (
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            {commLoading ? 'Loading communication data...' : 'Transitioning...'}
          </div>
        )}
      </div>

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
          onMoveEnd={handleMoveEnd}
          onNodeClick={selection.handleNodeClick}
          onNodeContextMenu={selection.handleNodeContextMenu}
          onEdgeMouseEnter={onEdgeMouseEnter}
          onEdgeMouseLeave={onEdgeMouseLeave}
          onEdgeClick={onEdgeClick}
          onNodeDragStart={viewMode === 'hierarchy' ? handleNodeDragStart : undefined}
          onNodeDrag={viewMode === 'hierarchy' ? handleNodeDrag : undefined}
          onNodeDragStop={viewMode === 'hierarchy' ? handleNodeDragStop : undefined}
          onPaneClick={selection.handlePaneClick}
          nodesConnectable={false}
          minZoom={0.1}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="var(--color-border)" gap={24} size={1} />
          {showMinimap && (
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
              nodeColor={(n: Node) => {
                if (n.type === 'owner') return 'var(--so-minimap-node-owner)'
                if (n.type === 'department') return 'var(--so-minimap-node-dept)'
                return 'var(--so-minimap-node-agent)'
              }}
              nodeStrokeColor={(n: Node) => (n.type === 'department' ? 'var(--so-minimap-stroke)' : 'transparent')}
              nodeStrokeWidth={MINIMAP_STROKE_WIDTH}
              nodeBorderRadius={MINIMAP_NODE_BORDER_RADIUS}
            />
          )}
        </ReactFlow>

        {filter.overlay}

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

      <ConfirmDialog
        open={selection.deleteConfirm !== null}
        onOpenChange={(open) => { if (!open) selection.setDeleteConfirm(null) }}
        title={`Delete "${selection.deleteConfirm?.label}"?`}
        description="This action cannot be undone."
        variant="destructive"
        confirmLabel="Delete"
        onConfirm={selection.confirmDelete}
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
          <Link to={ROUTES.ORG_EDIT}>Edit Organization</Link>
        </Button>
      </div>

      <ErrorBoundary level="section">
        <ReactFlowProvider>
          <OrgChartInner />
        </ReactFlowProvider>
      </ErrorBoundary>
    </div>
  )
}
