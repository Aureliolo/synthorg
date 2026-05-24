import { useCallback, useEffect, useMemo } from 'react'
import { createLogger } from '@/lib/logger'
import type { Node, Edge } from '@xyflow/react'
import { useCompanyStore } from '@/stores/company'
import { useAgentsStore } from '@/stores/agents'
import { useAuthStore } from '@/stores/auth'
import { useOrgChartPrefs } from '@/stores/org-chart-prefs'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import { useCommunicationEdges } from '@/hooks/useCommunicationEdges'
import { buildOrgTree, type OwnerInfo } from '@/pages/org/build-org-tree'
import { applyDagreLayout } from '@/pages/org/layout'
import { computeForceLayout } from '@/pages/org/force-layout'
import type { CommunicationLink } from '@/pages/org/aggregate-messages'
import type { CommunicationEdgeData } from '@/pages/org/CommunicationEdge'
import type { ViewMode } from '@/pages/org/OrgChartToolbar'
import type { WsChannel } from '@/api/types/websocket'

const log = createLogger('useOrgChartData')

const ORG_POLL_INTERVAL = 30_000
const ORG_CHANNELS = ['agents'] as const satisfies readonly WsChannel[]

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
  wsConnected: boolean
  wsSetupError: string | null
}

export interface UseOrgChartDataOptions {
  viewMode?: ViewMode
  /**
   * Department group IDs that are currently collapsed.  Child agents
   * of collapsed depts are filtered out BEFORE the dagre layout pass
   * so the dept box's computed height shrinks to header-only -- no
   * wasted space below the header where agents would have been.
   */
  collapsedDeptIds?: ReadonlySet<string>
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
  const freeNodes = agentNodes.map((n) => ({ ...n, parentId: undefined }))
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
}

interface DeriveViewArgs {
  readonly tree: OrgTree
  readonly viewMode: ViewMode
  readonly collapsedDeptIds?: ReadonlySet<string>
  readonly commLinks: CommunicationLink[]
  readonly prefs: DagrePrefs
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
    return { ...force, allNodes }
  }
  const layoutNodes = applyDagreLayout(args.tree.nodes, args.tree.edges, args.prefs)
  return { nodes: layoutNodes, edges: args.tree.edges, allNodes }
}

/**
 * Sequential initial fetch: department health depends on config being
 * loaded. Polling starts only after the initial fetch completes (or
 * fails) so we never race the first response.
 */
function useOrgInitialFetch(start: () => void, stop: () => void): void {
  useEffect(() => {
    const companyStore = useCompanyStore.getState()
    companyStore.fetchCompanyData().then(() => {
      if (useCompanyStore.getState().config) {
        companyStore.fetchDepartmentHealths().catch((err: unknown) => {
          log.warn('fetchDepartmentHealths failed:', err)
        })
      }
      start()
    }).catch((err: unknown) => {
      log.warn('fetchCompanyData failed:', err)
      start()
    })
    return () => stop()
    // eslint-disable-next-line @eslint-react/exhaustive-deps -- mount-only effect; start / stop are stable
  }, [])
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

  // Visual prefs that affect how much space the dept card chrome
  // takes up.  Passed through to `applyDagreLayout` so the reserved
  // header/footer space matches whatever the user currently has
  // toggled on -- no dead whitespace when budget bar / status dots
  // / add agent are off.
  const showBudgetBar = useOrgChartPrefs((s) => s.showBudgetBar)
  const showStatusDots = useOrgChartPrefs((s) => s.showStatusDots)
  const showAddAgentButton = useOrgChartPrefs((s) => s.showAddAgentButton)

  // Synthesise owner list from the current session user.  Designed
  // as an array so #1082 (multi-user ownership + per-dept admins)
  // can pass multiple owners without changing this shape -- today
  // it is exactly one element.
  const owners = useMemo<OwnerInfo[]>(() => {
    if (!currentUser) return []
    return [{ id: currentUser.id, displayName: currentUser.username }]
  }, [currentUser])

  const pollFn = useCallback(async () => {
    await useCompanyStore.getState().fetchDepartmentHealths()
  }, [])
  const polling = usePolling(pollFn, ORG_POLL_INTERVAL)
  useOrgInitialFetch(polling.start, polling.stop)

  // WebSocket bindings for real-time updates
  const bindings: ChannelBinding[] = useMemo(
    () =>
      ORG_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          useCompanyStore.getState().updateFromWsEvent(event)
          useAgentsStore.getState().updateFromWsEvent(event)
        },
      })),
    [],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({
    bindings,
  })

  // Communication data for force view (only fetched when needed)
  const { links: commLinks, loading: commLoading, error: commError, truncated: commTruncated } = useCommunicationEdges(
    viewMode === 'force',
  )

  const { nodes, edges, allNodes } = useMemo(() => {
    if (!config) return { nodes: [], edges: [], allNodes: [] }
    const tree = buildOrgTree(
      config, runtimeStatuses, departmentHealths, owners, [], currentUser?.id,
    )
    return _deriveView({
      tree,
      viewMode,
      collapsedDeptIds,
      commLinks,
      prefs: { showBudgetBar, showStatusDots, showAddAgentButton },
    })
  }, [
    config, runtimeStatuses, departmentHealths, viewMode, commLinks, owners,
    collapsedDeptIds, showBudgetBar, showStatusDots, showAddAgentButton,
    currentUser?.id,
  ])

  return {
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
  }
}
