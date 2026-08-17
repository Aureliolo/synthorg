import { useCallback, useEffect, useMemo } from 'react'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import type { Node, Edge } from '@xyflow/react'
import { useCompanyStore } from '@/stores/company'
import { useAgentsStore } from '@/stores/agents'
import { useAuthStore } from '@/stores/auth'
import { useOrgChartPrefs } from '@/stores/org-chart-prefs'
import { useThemeStore, type Density } from '@/stores/theme'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import { useCommunicationEdges } from '@/hooks/useCommunicationEdges'
import { buildOrgTree, type OwnerInfo } from '@/pages/org/build-org-tree'
import { applyDagreLayout } from '@/pages/org/layout'
import {
  applyHierarchyRouting,
  hierarchyRoutingPlan,
  type HierarchyRoutingPlan,
} from '@/pages/org/route-hierarchy'
import { computeForceLayout } from '@/pages/org/force-layout'
import type { CommunicationLink } from '@/pages/org/aggregate-messages'
import type { CommunicationEdgeData } from '@/pages/org/CommunicationEdge'
import type { ViewMode } from '@/pages/org/OrgChartToolbar'
import type { WsChannel } from '@/api/types/websocket'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { CompanyConfig } from '@/api/types/org'
import type { AgentRuntimeStatus } from '@/utils/agent-status'

const log = createLogger('useOrgChartData')

const ORG_POLL_INTERVAL = 30_000
const ORG_CHANNELS = ['agents'] as const satisfies readonly WsChannel[]

// The structural tree the layout is measured from carries no live readings,
// so a status or health frame cannot invalidate the cached placement.
const NO_RUNTIME_STATUSES: Record<string, AgentRuntimeStatus> = {}
const NO_DEPARTMENT_HEALTHS: readonly DepartmentHealth[] = []

export interface UseOrgChartDataReturn {
  nodes: Node[]
  edges: Edge[]
  /** All tree nodes before collapse filtering -- used for search indexing. */
  allNodes: Node[]
  loading: boolean
  error: string | null
  commLoading: boolean
  commError: string | null
  commTruncated: boolean
  refetchComm: () => void
  wsConnected: boolean
  wsSetupError: string | null
}


function buildCommunicationEdges(
  links: CommunicationLink[],
): Edge[] {
  const maxVolume = Math.max(1, ...links.map((l) => l.volume))
  return links.map((link) => ({
    id: `comm:${encodeURIComponent(link.source)}::${encodeURIComponent(link.target)}`,
    source: link.source,
    target: link.target,
    type: 'communication',
    data: {
      volume: link.volume,
      frequency: link.frequency,
      maxVolume,
    } satisfies CommunicationEdgeData,
  }))
}

type OrgTree = ReturnType<typeof buildOrgTree>

/**
 * Strip child agents whose parent department is collapsed, mark the
 * collapsed dept nodes with `isCollapsed: true`, and prune edges that
 * pointed at removed nodes. Mutates the tree in place so the dagre
 * pass that follows sees the smaller set.
 */
function _applyCollapse(tree: OrgTree, collapsedDeptIds: ReadonlySet<string>): void {
  tree.nodes = tree.nodes
    .filter((n) => !(n.parentId && collapsedDeptIds.has(n.parentId)))
    .map((n) =>
      n.type === 'department' && collapsedDeptIds.has(n.id)
        ? { ...n, data: { ...n.data, isCollapsed: true } }
        : n,
    )
  const remainingNodeIds = new Set(tree.nodes.map((n) => n.id))
  tree.edges = tree.edges.filter(
    (e) => remainingNodeIds.has(e.source) && remainingNodeIds.has(e.target),
  )
}

/**
 * Force view: only agent/ceo nodes (no department groups, no owner
 * nodes, no hidden layout edges). Communication view is about agent-
 * to-agent message flow, so the hierarchy scaffold is intentionally
 * stripped.
 */
function _buildForceView(
  tree: OrgTree,
  commLinks: CommunicationLink[],
): { nodes: Node[]; edges: Edge[] } {
  const agentNodes = tree.nodes.filter((n) => n.type === 'agent' || n.type === 'ceo')
  const freeNodes = agentNodes.map((n) => {
    const { parentId, ...rest } = n
    void parentId
    return rest
  })
  const visibleIds = new Set(freeNodes.map((n) => n.id))
  const filteredLinks = commLinks.filter(
    (l) => visibleIds.has(l.source) && visibleIds.has(l.target),
  )
  return {
    nodes: computeForceLayout(freeNodes, filteredLinks),
    edges: buildCommunicationEdges(filteredLinks),
  }
}

interface DagrePrefs {
  readonly showBudgetBar: boolean
  readonly showStatusDots: boolean
  readonly showAddAgentButton: boolean
  readonly density: Density
}

/**
 * Everything the layout needs to reserve exactly the card chrome that will be
 * rendered: the toggles that add or remove a header row, and the density the
 * cards' `p-card` padding resolves at.
 */
function useDagrePrefs(): DagrePrefs {
  const showBudgetBar = useOrgChartPrefs((s) => s.showBudgetBar)
  const showStatusDots = useOrgChartPrefs((s) => s.showStatusDots)
  const showAddAgentButton = useOrgChartPrefs((s) => s.showAddAgentButton)
  const density = useThemeStore((s) => s.density)
  return useMemo(
    () => ({ showBudgetBar, showStatusDots, showAddAgentButton, density }),
    [showBudgetBar, showStatusDots, showAddAgentButton, density],
  )
}

/** What the layout assigns to a node. None of it depends on live status. */
interface PlacedNode {
  readonly position: { x: number; y: number }
  readonly width: number | undefined
  readonly height: number | undefined
  readonly style: Node['style']
}
/**
 * The geometry one structural layout produces: where each node lands, and the
 * corridors the connectors between them should follow.
 *
 * Both are answers about the same placed geometry, so they are worked out
 * together and cached together. Routing searches every source's rows for a
 * clear riser, and none of that moves when an agent's status does.
 */
interface LayoutSnapshot {
  readonly placement: ReadonlyMap<string, PlacedNode>
  readonly routing: HierarchyRoutingPlan
}

function _snapshotOf(nodes: readonly Node[], edges: readonly Edge[]): LayoutSnapshot {
  const placement = new Map<string, PlacedNode>()
  for (const node of nodes) {
    placement.set(node.id, {
      position: node.position,
      width: node.width,
      height: node.height,
      style: node.style,
    })
  }
  return { placement, routing: hierarchyRoutingPlan(nodes, edges) }
}

/**
 * Re-attach a cached placement to a freshly built (live-status) tree.
 *
 * Both trees come from the same config, owners and collapse set, so their id
 * sets match; the snapshot tree only omits runtime statuses and department
 * health. Should node emission ever start depending on a field only the live
 * tree carries, the misses are collected rather than logged here: this runs
 * during render, so a warning from inside it fires twice under StrictMode and
 * again on every unrelated re-render. The caller reports them from an effect.
 */
function _placeNodes(
  nodes: readonly Node[],
  snapshot: LayoutSnapshot,
): { placed: Node[]; unplaced: string[] } {
  const unplaced: string[] = []
  const result = nodes.map((node) => {
    const placement = snapshot.placement.get(node.id)
    if (!placement) {
      unplaced.push(node.id)
      return node
    }
    const next: Node = { ...node, position: placement.position }
    if (placement.width !== undefined) next.width = placement.width
    if (placement.height !== undefined) next.height = placement.height
    if (placement.style !== undefined) next.style = placement.style
    return next
  })
  return { placed: result, unplaced }
}

/** Report an unplaced node once per distinct set, off the render path. */
function useUnplacedWarning(unplaced: readonly string[]): void {
  const key = unplaced.join(',')
  useEffect(() => {
    if (key === '') return
    // The ids are built from operator-authored department and agent names.
    log.warn('nodes missing from the layout snapshot, rendered unplaced:',
      sanitizeForLog(key))
  }, [key])
}

interface LayoutSnapshotArgs {
  readonly config: CompanyConfig | null
  readonly viewMode: ViewMode
  readonly collapsedDeptIds?: ReadonlySet<string> | undefined
  readonly owners: readonly OwnerInfo[]
  readonly currentUserId: string | undefined
  readonly prefs: DagrePrefs
}

/**
 * Cache where the layout puts every node, keyed on the org's structure alone.
 *
 * The layout reads a node's type, its parent, and the isDeptLead /
 * isRootDepartment flags, all of which come from the company config. Live
 * agent status and department health only ever reach a card's rendered
 * `data`, so measuring from a status-free tree yields the same placement and
 * a status frame then costs one O(n) re-placement instead of relaying out
 * every unit.
 */
function useLayoutSnapshot(args: LayoutSnapshotArgs): LayoutSnapshot | null {
  const { config, viewMode, collapsedDeptIds, owners, currentUserId, prefs } = args
  return useMemo(() => {
    if (!config || viewMode !== 'hierarchy') return null
    const tree = buildOrgTree({
      config,
      runtimeStatuses: NO_RUNTIME_STATUSES,
      departmentHealths: NO_DEPARTMENT_HEALTHS,
      owners,
      currentUserId,
    })
    if (collapsedDeptIds && collapsedDeptIds.size > 0) {
      _applyCollapse(tree, collapsedDeptIds)
    }
    return _snapshotOf(applyDagreLayout(tree.nodes, tree.edges, prefs), tree.edges)
  }, [config, viewMode, collapsedDeptIds, owners, currentUserId, prefs])
}

interface DeriveViewArgs {
  readonly tree: OrgTree
  readonly viewMode: ViewMode
  readonly collapsedDeptIds?: ReadonlySet<string> | undefined
  readonly commLinks: CommunicationLink[]
  readonly layoutSnapshot: LayoutSnapshot | null
}

/**
 * Derive the rendered React Flow nodes/edges from a built org tree.
 * Returns `allNodes` snapshot BEFORE collapse filtering so consumers
 * (e.g. search) can index every node regardless of which departments
 * are collapsed.
 */
function _deriveView(args: DeriveViewArgs): {
  nodes: Node[]
  edges: Edge[]
  allNodes: Node[]
  unplaced: string[]
} {
  const allNodes = [...args.tree.nodes]
  if (
    args.viewMode === 'hierarchy'
    && args.collapsedDeptIds
    && args.collapsedDeptIds.size > 0
  ) {
    _applyCollapse(args.tree, args.collapsedDeptIds)
  }
  if (args.viewMode === 'force') {
    const force = _buildForceView(args.tree, args.commLinks)
    return { ...force, allNodes, unplaced: [] }
  }
  if (!args.layoutSnapshot) {
    return {
      nodes: args.tree.nodes,
      edges: args.tree.edges,
      allNodes,
      unplaced: [],
    }
  }
  // The routing came with the placement, because it is an answer about the same
  // geometry: an edge component sees only its own two endpoints, so it cannot
  // tell that its target sits on a later row of a block and that dropping
  // straight would cross the row above. Applying it here is a per-edge lookup.
  const { placed, unplaced } = _placeNodes(args.tree.nodes, args.layoutSnapshot)
  return {
    nodes: placed,
    edges: applyHierarchyRouting(args.tree.edges, args.layoutSnapshot.routing),
    allNodes,
    unplaced,
  }
}

/**
 * Sequential initial fetch: department health depends on config being
 * loaded. Polling starts only after the initial fetch completes (or
 * fails) so we never race the first response.
 */
function useOrgInitialFetch(start: () => void, stop: () => void): void {
  useEffect(() => {
    // Mounted flag prevents `start()` from arming a polling timer
    // after the component has unmounted (the fetch promises resolve
    // asynchronously, so without the guard a quick navigation away
    // orphans the polling loop). Cleanup also calls `stop()` so any
    // in-flight schedule is torn down even on the happy path.
    let mounted = true
    const companyStore = useCompanyStore.getState()
    void companyStore.fetchCompanyData()
      .then(async () => {
        if (!mounted) return
        // Await the initial health fetch BEFORE arming polling, so
        // the first polling tick cannot overlap the initial health
        // load and produce out-of-order store writes.
        if (useCompanyStore.getState().config) {
          try {
            await companyStore.fetchDepartmentHealths()
          } catch (err: unknown) {
            log.warn('fetchDepartmentHealths failed:', err)
          }
        }
      })
      .catch((err: unknown) => {
        log.warn('fetchCompanyData failed:', err)
      })
      .finally(() => {
        if (mounted) start()
      })
    return () => {
      mounted = false
      stop()
    }
  }, [start, stop])
}

/**
 * Keep the org data fresh: poll department health, and fold every WS frame on
 * the agents channel into the company and agents stores.
 */
function useOrgLiveSync(): { wsConnected: boolean; wsSetupError: string | null } {
  const { skipIfFresh, markFresh } = useFreshnessGate()
  const pollFn = useCallback(async () => {
    await useCompanyStore.getState().fetchDepartmentHealths()
  }, [])
  const polling = usePolling(pollFn, ORG_POLL_INTERVAL, { skipIfFresh })
  useOrgInitialFetch(polling.start, polling.stop)

  const bindings: ChannelBinding[] = useMemo(
    () =>
      ORG_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          // Only an org mutation refetches department health, and that is all
          // the poll below fetches. A status frame carries its own update over
          // the socket, so counting it as freshness would let a busy org
          // suppress the health poll indefinitely.
          if (useCompanyStore.getState().updateFromWsEvent(event)) markFresh()
          useAgentsStore.getState().updateFromWsEvent(event)
        },
      })),
    [markFresh],
  )
  const { connected, setupError } = useWebSocket({ bindings })
  return { wsConnected: connected, wsSetupError: setupError }
}

export function useOrgChartData(
  viewMode: ViewMode = 'hierarchy',
  collapsedDeptIds?: ReadonlySet<string>,
): UseOrgChartDataReturn {
  const config = useCompanyStore((s) => s.config)
  const departmentHealths = useCompanyStore((s) => s.departmentHealths)
  const loading = useCompanyStore((s) => s.loading)
  const error = useCompanyStore((s) => s.error)
  const runtimeStatuses = useAgentsStore((s) => s.runtimeStatuses)
  const currentUser = useAuthStore((s) => s.user)

  const dagrePrefs = useDagrePrefs()

  // Synthesise owner list from the current session user.  Designed
  // as an array so future multi-user ownership (per-dept admins) can
  // pass multiple owners without changing this shape; today it is
  // exactly one element.
  const owners = useMemo<OwnerInfo[]>(() => {
    if (!currentUser) return []
    return [{ id: currentUser.id, displayName: currentUser.username }]
  }, [currentUser])

  const { wsConnected, wsSetupError } = useOrgLiveSync()

  // Communication data for force view (only fetched when needed)
  const {
    links: commLinks,
    loading: commLoading,
    error: commError,
    truncated: commTruncated,
    refetch: refetchComm,
  } = useCommunicationEdges(viewMode === 'force')

  const layoutSnapshot = useLayoutSnapshot({
    config,
    viewMode,
    collapsedDeptIds,
    owners,
    currentUserId: currentUser?.id,
    prefs: dagrePrefs,
  })

  const { nodes, edges, allNodes, unplaced } = useMemo(() => {
    if (!config) return { nodes: [], edges: [], allNodes: [], unplaced: [] }
    const tree = buildOrgTree({
      config,
      runtimeStatuses,
      departmentHealths,
      owners,
      currentUserId: currentUser?.id,
    })
    return _deriveView({
      tree,
      viewMode,
      collapsedDeptIds,
      commLinks,
      layoutSnapshot,
    })
  }, [
    config, runtimeStatuses, departmentHealths, viewMode, commLinks, owners,
    collapsedDeptIds, layoutSnapshot, currentUser?.id,
  ])
  useUnplacedWarning(unplaced)

  return {
    nodes,
    edges,
    allNodes,
    loading,
    error,
    commLoading,
    commError,
    commTruncated,
    refetchComm,
    wsConnected,
    wsSetupError,
  }
}
