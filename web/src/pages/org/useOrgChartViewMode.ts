import { useLayoutEffect, useReducer, useRef } from 'react'
import type { Edge, Node } from '@xyflow/react'
import { createLogger } from '@/lib/logger'
import { prefersReducedMotion, TRANSITION_SLOW_MS } from '@/lib/motion'
import type { ViewMode } from './OrgChartToolbar'

const log = createLogger('OrgChart:ViewMode')
const TRANSITION_DURATION_MS = TRANSITION_SLOW_MS

function tweenSlowEase(t: number): number {
  if (t <= 0) return 0
  if (t >= 1) return 1
  return t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2
}

/**
 * Lift a node's position into the absolute coordinate space the
 * canvas uses for un-parented nodes.  When the source frame has a
 * ``parentId`` and the target does not (the hierarchy → force
 * switch on the org chart), the recorded ``position`` is RELATIVE
 * to the dept group; treating it as absolute would teleport the
 * agent to a corner near origin and make the transition look like
 * the nodes fly across the screen.  Walks the parent chain so
 * nested teams resolve correctly.
 */
function resolveAbsolutePosition(node: Node, byId: Map<string, Node>): { x: number; y: number } {
  let x = node.position.x
  let y = node.position.y
  let parentId = node.parentId
  const visited = new Set<string>([node.id])
  while (parentId && !visited.has(parentId)) {
    visited.add(parentId)
    const parent = byId.get(parentId)
    if (!parent) break
    x += parent.position.x
    y += parent.position.y
    parentId = parent.parentId
  }
  return { x, y }
}

function interpolateNodes(from: Node[], to: Node[], progress: number): Node[] {
  const toMap = new Map(to.map((n) => [n.id, n]))
  const fromMap = new Map(from.map((n) => [n.id, n]))
  const result: Node[] = []

  for (const target of to) {
    const source = fromMap.get(target.id)
    if (source) {
      // Lift both positions into absolute space whenever the parent
      // chain differs between frames; without this, swapping the
      // hierarchy layout (parent-relative coords for agents inside
      // dept groups) for the force layout (parent-less absolute
      // coords) interpolates between two different coordinate
      // systems and produces the "fly across the screen" artefact.
      const needsLift = source.parentId !== target.parentId
      const sourcePos = needsLift ? resolveAbsolutePosition(source, fromMap) : source.position
      const targetPos = needsLift ? resolveAbsolutePosition(target, toMap) : target.position
      result.push({
        ...target,
        position: {
          x: sourcePos.x + (targetPos.x - sourcePos.x) * progress,
          y: sourcePos.y + (targetPos.y - sourcePos.y) * progress,
        },
      })
    } else {
      result.push(target)
    }
  }

  if (progress < 1) {
    for (const source of from) {
      if (!toMap.has(source.id)) {
        result.push(source)
      }
    }
  }
  return result
}

interface TransitionState {
  displayNodes: Node[]
  displayEdges: Edge[]
  transitioning: boolean
}

type TransitionAction =
  | { type: 'snap'; nodes: Node[]; edges: Edge[] }
  | { type: 'start'; edges: Edge[] }
  | { type: 'frame'; nodes: Node[] }
  | { type: 'end'; nodes: Node[] }

function transitionReducer(state: TransitionState, action: TransitionAction): TransitionState {
  switch (action.type) {
    case 'snap':
      return { displayNodes: action.nodes, displayEdges: action.edges, transitioning: false }
    case 'start':
      return { ...state, displayEdges: action.edges, transitioning: true }
    case 'frame':
      return { ...state, displayNodes: action.nodes }
    case 'end':
      return { ...state, displayNodes: action.nodes, transitioning: false }
  }
}

export interface OrgChartViewModeResult {
  displayNodes: Node[]
  displayEdges: Edge[]
  transitioning: boolean
}

/**
 * Animates the transition between node layouts when the page's view mode
 * changes. The page owns the `ViewMode` state (it is read before fetch, and
 * again after fetched nodes/edges arrive) -- this hook is controlled: it
 * only re-runs the animation when `nodes`/`edges` change. Uses a reducer +
 * layout effect so the first paint never shows a mid-transition state.
 *
 * The `_viewMode` parameter is accepted for API symmetry and to let
 * callers make the re-animation trigger explicit; it is not read, but
 * changing it updates the effect dependency list without adding a
 * separate prop.
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- viewMode is part of the controlled API surface
export function useOrgChartViewMode(nodes: Node[], edges: Edge[], _viewMode: ViewMode): OrgChartViewModeResult {
  const [transition, dispatch] = useReducer(transitionReducer, {
    displayNodes: [],
    displayEdges: [],
    transitioning: false,
  })
  const prevNodesRef = useRef<Node[]>([])
  const animFrameRef = useRef<number | null>(null)

  useLayoutEffect(() => {
    if (animFrameRef.current !== null) {
      cancelAnimationFrame(animFrameRef.current)
      animFrameRef.current = null
    }

    const fromNodes = transition.displayNodes.length > 0 ? transition.displayNodes : prevNodesRef.current

    if (nodes.length === 0 || prefersReducedMotion() || fromNodes.length === 0) {
      prevNodesRef.current = nodes
      dispatch({ type: 'snap', nodes, edges })
      return
    }

    prevNodesRef.current = nodes
    dispatch({ type: 'start', edges })

    const startTime = performance.now()

    function animate(now: number) {
      const elapsed = now - startTime
      const rawProgress = Math.min(elapsed / TRANSITION_DURATION_MS, 1)
      const easedProgress = tweenSlowEase(rawProgress)

      if (rawProgress < 1) {
        dispatch({ type: 'frame', nodes: interpolateNodes(fromNodes, nodes, easedProgress) })
        animFrameRef.current = requestAnimationFrame(animate)
      } else {
        dispatch({ type: 'end', nodes })
        animFrameRef.current = null
      }
    }

    animFrameRef.current = requestAnimationFrame(animate)

    return () => {
      if (animFrameRef.current !== null) {
        cancelAnimationFrame(animFrameRef.current)
        animFrameRef.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- transition.displayNodes is read for starting position only; including it would cause infinite loops
  }, [nodes, edges])

  useLayoutEffect(() => {
    log.debug('Transition state', {
      nodes: nodes.length,
      edges: edges.length,
      displayNodes: transition.displayNodes.length,
      transitioning: transition.transitioning,
    })
  }, [nodes.length, edges.length, transition.displayNodes.length, transition.transitioning])

  return {
    displayNodes: transition.displayNodes,
    displayEdges: transition.displayEdges,
    transitioning: transition.transitioning,
  }
}
