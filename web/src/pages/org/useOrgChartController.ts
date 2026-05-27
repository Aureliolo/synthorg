import { useCallback, useEffect, useMemo, useState } from 'react'
import { useReactFlow } from '@xyflow/react'
import { GitBranch } from 'lucide-react'
import { useNavigate } from 'react-router'
import { createLogger } from '@/lib/logger'
import { TRANSITION_SLOW_MS } from '@/lib/motion'
import { useOrgChartData } from '@/hooks/useOrgChartData'
import { useRegisterCommands } from '@/hooks/useCommandPalette'
import { useOrgChartPrefs } from '@/stores/org-chart-prefs'
import { ROUTES } from '@/router/routes'
import { useOrgChartDragDrop, type OrgChartDragDropResult } from './useOrgChartDragDrop'
import {
  useOrgChartEdgeInteraction,
  type OrgChartEdgeInteractionResult,
} from './OrgChartEdgeInteraction'
import { useOrgChartFilter } from './OrgChartFilter'
import { useOrgChartSelection, type OrgChartSelectionResult } from './useOrgChartSelection'
import { useOrgChartViewMode } from './useOrgChartViewMode'
import { useOrgChartCollapse } from './useOrgChartCollapse'
import { useOrgChartPngExport, type OrgChartPngExportResult } from './useOrgChartPngExport'
import {
  useOrgChartRenderModel,
  type OrgChartEdgeData,
} from './useOrgChartRenderModel'
import type { Edge, Node } from '@xyflow/react'

const log = createLogger('OrgChart')

const VIEWPORT_KEY = 'synthorg:orgchart:viewport'

interface ViewportState {
  x: number
  y: number
  zoom: number
}

function saveViewport(viewport: ViewportState) {
  try {
    localStorage.setItem(VIEWPORT_KEY, JSON.stringify(viewport))
  } catch (err) {
    log.warn('Failed to save viewport:', err)
  }
}

type FitViewFn = (options?: { padding?: number; duration?: number }) => unknown

/** Re-fit the viewport once a view-mode change or layout settles. */
function useFitOnViewSettle(
  viewMode: 'hierarchy' | 'force',
  transitioning: boolean,
  displayCount: number,
  fitView: FitViewFn,
): void {
  useEffect(() => {
    if (transitioning) return
    if (displayCount === 0) return
    const id = requestAnimationFrame(() => {
      void fitView({ padding: 0.2, duration: TRANSITION_SLOW_MS })
    })
    return () => cancelAnimationFrame(id)
  }, [viewMode, transitioning, displayCount, fitView])
}

/** Register the "Fit to View" command-palette entry. */
function useFitViewCommand(fitView: FitViewFn): void {
  const commands = useMemo(
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
  useRegisterCommands(commands)
}

export interface OrgChartController {
  data: ReturnType<typeof useOrgChartData>
  view: ReturnType<typeof useOrgChartViewMode>
  selection: OrgChartSelectionResult
  drag: OrgChartDragDropResult
  png: OrgChartPngExportResult
  edge: OrgChartEdgeInteractionResult<OrgChartEdgeData>
  renderedNodes: Node[]
  renderedEdges: Edge[]
  viewMode: 'hierarchy' | 'force'
  handleViewModeChange: (mode: 'hierarchy' | 'force') => void
  onFitView: () => void
  onZoomIn: () => void
  onZoomOut: () => void
  handleMoveEnd: (event: unknown, viewport: ViewportState) => void
  dragEnabled: boolean
  showMinimap: boolean
  filterOverlay: React.ReactNode
  announcement: { id: number; text: string } | null
  goToOrgEdit: () => void
}

/** Wire every org-chart hook into one controller for the page view. */
export function useOrgChartController(): OrgChartController {
  const { collapsedDepts, toggleDeptCollapsed } = useOrgChartCollapse()

  // Drag hooks call `announce(text)`. The incrementing `id` makes
  // identical consecutive messages still re-fire the aria-live region.
  const [announcement, setAnnouncement] = useState<{ id: number; text: string } | null>(null)
  const announce = useCallback((text: string) => {
    setAnnouncement((prev) => ({ id: (prev?.id ?? 0) + 1, text }))
  }, [])

  const { fitView, zoomIn, zoomOut } = useReactFlow()
  const navigate = useNavigate()
  const showMinimap = useOrgChartPrefs((s) => s.showMinimap)
  const [viewMode, setViewMode] = useState<'hierarchy' | 'force'>('hierarchy')

  const data = useOrgChartData(viewMode, collapsedDepts)
  const view = useOrgChartViewMode(data.nodes, data.edges, viewMode)
  const handleViewModeChange = useCallback((mode: 'hierarchy' | 'force') => {
    setViewMode(mode)
  }, [])

  useFitOnViewSettle(viewMode, view.transitioning, view.displayNodes.length, fitView)

  // Fall back to the raw `nodes` until a layout change settles.
  const sourceNodes = view.displayNodes.length > 0 ? view.displayNodes : data.nodes

  const drag = useOrgChartDragDrop({ viewMode, displayNodes: sourceNodes, announce })
  const selection = useOrgChartSelection(sourceNodes)
  const filter = useOrgChartFilter(data.allNodes)
  useFitViewCommand(fitView)

  const handleMoveEnd = useCallback((_event: unknown, viewport: ViewportState) => {
    saveViewport(viewport)
  }, [])

  const png = useOrgChartPngExport(fitView)
  const model = useOrgChartRenderModel({
    sourceNodes,
    displayEdges: view.displayEdges,
    edges: data.edges,
    dragOverDeptId: drag.dragOverDeptId,
    highlightedNodeIds: filter.highlightedNodeIds,
    toggleDeptCollapsed,
    viewMode,
  })
  const edge = useOrgChartEdgeInteraction<OrgChartEdgeData>({ edges: model.edgesWithParticles })

  const onFitView = useCallback(() => void fitView({ padding: 0.2 }), [fitView])
  const onZoomIn = useCallback(() => void zoomIn(), [zoomIn])
  const onZoomOut = useCallback(() => void zoomOut(), [zoomOut])
  const goToOrgEdit = useCallback(() => void navigate(ROUTES.ORG_EDIT), [navigate])

  return {
    data,
    view,
    selection,
    drag,
    png,
    edge,
    renderedNodes: model.renderedNodes,
    renderedEdges: edge.edgesWithHoverState,
    viewMode,
    handleViewModeChange,
    onFitView,
    onZoomIn,
    onZoomOut,
    handleMoveEnd,
    dragEnabled: viewMode === 'hierarchy',
    showMinimap,
    filterOverlay: filter.overlay,
    announcement,
    goToOrgEdit,
  }
}
