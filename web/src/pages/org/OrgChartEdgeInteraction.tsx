import { useCallback, useMemo, useState } from 'react'
import type { Edge, EdgeMouseHandler } from '@xyflow/react'

/** Minimum shape an edge's `data` must satisfy to participate in hover
 *  tracking. Any richer edge-data type (e.g. `OrgChartEdgeData`) can be used
 *  as long as it declares an optional `hovered` slot. */
export interface EdgeHoverData extends Record<string, unknown> {
  hovered?: boolean
}

export interface OrgChartEdgeInteractionResult<T extends EdgeHoverData> {
  /** Edge currently hovered by the pointer, or `null` when none. */
  hoveredEdgeId: string | null
  /** Edges enriched with a `hovered: true` flag on `data` for the hovered
   *  entry; all other edges pass through unchanged. */
  edgesWithHoverState: Edge<T>[]
  onEdgeMouseEnter: EdgeMouseHandler
  onEdgeMouseLeave: EdgeMouseHandler
  onEdgeClick: EdgeMouseHandler
}

interface UseOrgChartEdgeInteractionArgs<T extends EdgeHoverData> {
  edges: readonly Edge<T>[]
  onEdgeSelected?: (edge: Edge<T>) => void
}

/**
 * Tracks pointer hover over org-chart edges and exposes ReactFlow-compatible
 * handlers plus an `edgesWithHoverState` array that mirrors the input edges
 * with a `hovered: true` flag on `data`. Edge components can read
 * `data.hovered` to change stroke weight/colour without re-subscribing the
 * whole graph. Click selection is optional and routed via `onEdgeSelected`.
 *
 * Generic over the edge-data type so callers keep their specific data shape
 * (e.g. `OrgChartEdgeData`) end-to-end without widening.
 */
export function useOrgChartEdgeInteraction<T extends EdgeHoverData>(
  args: UseOrgChartEdgeInteractionArgs<T>,
): OrgChartEdgeInteractionResult<T> {
  const { edges, onEdgeSelected } = args
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null)

  const onEdgeMouseEnter = useCallback<EdgeMouseHandler>((_, edge) => {
    setHoveredEdgeId(edge.id)
  }, [])

  const onEdgeMouseLeave = useCallback<EdgeMouseHandler>((_, edge) => {
    setHoveredEdgeId((current) => (current === edge.id ? null : current))
  }, [])

  const onEdgeClick = useCallback<EdgeMouseHandler>(
    (_, edge) => {
      onEdgeSelected?.(edge as Edge<T>)
    },
    [onEdgeSelected],
  )

  const edgesWithHoverState = useMemo<Edge<T>[]>(() => {
    if (hoveredEdgeId === null) return [...edges]
    return edges.map((edge) => {
      if (edge.id !== hoveredEdgeId) return edge
      // Spread the real edge data (a no-op when undefined) rather than
      // fabricating an empty ``{} as T``; only ``hovered`` is added.
      return { ...edge, data: { ...edge.data, hovered: true } as T }
    })
  }, [edges, hoveredEdgeId])

  return {
    hoveredEdgeId,
    edgesWithHoverState,
    onEdgeMouseEnter,
    onEdgeMouseLeave,
    onEdgeClick,
  }
}
