import { useMemo } from 'react'
import type { Edge, Node } from '@xyflow/react'
import { useOrgChartPrefs } from '@/stores/org-chart-prefs'
import { useLiveEdgeActivity } from '@/hooks/useLiveEdgeActivity'

/**
 * Shared edge-data shape for the org chart. Narrower than xyflow's
 * default `Record<string, unknown>` so edge data merges stay type-safe.
 */
export interface OrgChartEdgeData extends Record<string, unknown> {
  particlesVisible?: boolean
  hovered?: boolean
}

// Every field is optional, so an empty object is a valid base; using a typed
// constant keeps the merge below honest instead of asserting ``{} as ...``.
const DEFAULT_EDGE_DATA: OrgChartEdgeData = {}

interface DecorateOptions {
  dragOverDeptId: string | null
  highlightedNodeIds: Set<string> | null
  toggleDeptCollapsed: (deptId: string) => void
  viewMode: 'hierarchy' | 'force'
}

/** Apply (or clear) the dim-others opacity style for a rendered node. */
function applyDimStyle(next: Node, original: Node, dimmed: boolean): void {
  if (dimmed) {
    next.style = {
      ...original.style,
      opacity: 0.25,
      transition: `opacity var(--so-transition-dim) ease`,
    }
    return
  }
  if (original.style && typeof (original.style as { opacity?: number }).opacity === 'number') {
    const rest = { ...original.style } as Record<string, unknown>
    delete rest['opacity']
    next.style = rest
  }
}

/** Decorate a source node with drag-target, collapse, and dim state. */
function decorateNode(n: Node, opts: DecorateOptions): Node {
  const isDropTarget =
    opts.dragOverDeptId !== null && n.type === 'department' && n.id === opts.dragOverDeptId
  const dimmed = opts.highlightedNodeIds !== null && !opts.highlightedNodeIds.has(n.id)
  const next = { ...n }
  next.draggable = opts.viewMode === 'hierarchy' && n.type === 'agent'
  if (n.type === 'department') {
    next.data = { ...n.data, onToggleCollapsed: opts.toggleDeptCollapsed }
  }
  if (isDropTarget) {
    next.data = { ...next.data, isDropTarget: true }
  }
  applyDimStyle(next, n, dimmed)
  return next
}

export interface RenderModelArgs {
  sourceNodes: Node[]
  displayEdges: Edge[]
  edges: Edge[]
  dragOverDeptId: string | null
  highlightedNodeIds: Set<string> | null
  toggleDeptCollapsed: (deptId: string) => void
  viewMode: 'hierarchy' | 'force'
}

export interface RenderModelResult {
  renderedNodes: Node[]
  edgesWithParticles: Edge<OrgChartEdgeData>[]
}

/** Derive the decorated node list + particle-tagged edge list. */
export function useOrgChartRenderModel(args: RenderModelArgs): RenderModelResult {
  const { sourceNodes, displayEdges, edges, dragOverDeptId, highlightedNodeIds, toggleDeptCollapsed, viewMode } = args
  const particleFlowMode = useOrgChartPrefs((s) => s.particleFlowMode)

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

  const renderedNodes = useMemo(
    () =>
      sourceNodes.map((n) =>
        decorateNode(n, { dragOverDeptId, highlightedNodeIds, toggleDeptCollapsed, viewMode }),
      ),
    [sourceNodes, dragOverDeptId, highlightedNodeIds, toggleDeptCollapsed, viewMode],
  )

  const edgesWithParticles = useMemo<Edge<OrgChartEdgeData>[]>(
    () =>
      displayEdges.map((e) => {
        const particlesVisible =
          particleFlowMode === 'always'
            ? true
            : particleFlowMode === 'live'
              ? liveActiveEdgeIds.has(e.id)
              : false
        const existing = e.data ?? DEFAULT_EDGE_DATA
        return { ...e, data: { ...existing, particlesVisible } }
      }),
    [displayEdges, particleFlowMode, liveActiveEdgeIds],
  )

  return { renderedNodes, edgesWithParticles }
}
