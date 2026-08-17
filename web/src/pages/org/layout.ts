import type { Node, Edge } from '@xyflow/react'
import type { Density } from '@/stores/theme'
import {
  type GroupResult,
  type LayoutOptions,
  DEFAULT_NODE_HEIGHT,
  DEFAULT_NODE_SEP,
  DEFAULT_NODE_WIDTH,
  DEFAULT_RANK_SEP,
  EMPTY_GROUP_HEIGHT,
  EMPTY_GROUP_MIN_WIDTH,
  cardPaddingFor,
  sizeAgentNodes,
} from './layout-shared'
import { flowIntoGrid } from './layout-grid'
import { planRanks } from './layout-clusters'
import { type DagreParams, runDagreLayout } from './layout-graph'
import {
  type DeptChromePrefs,
  type HierarchyPlan,
  collectRootGroupIds,
  planHierarchy,
} from './layout-groups'
import {
  anchorLayout,
  centerOwnersOverRoot,
  enforceVerticalGaps,
  placeDepartmentGrid,
} from './layout-passes'

export type { LayoutOptions } from './layout-shared'

/**
 * Grid fallback for a chart with nothing to rank: no department to wrap the
 * nodes in and no edge to order them by.
 *
 * Reached before the operator has created a department, when the canvas holds
 * a handful of unrelated cards. A chart whose departments are merely unstaffed
 * does NOT come here: those boxes still carry the order the operator chose and
 * still need the spine anchored, and this grid can express neither.
 */
function layoutEmptyChart(nodes: Node[]): Node[] {
  const sizes = nodes.map((n) => ({
    id: n.id,
    w: n.type === 'owner' ? DEFAULT_NODE_WIDTH : EMPTY_GROUP_MIN_WIDTH,
    h: n.type === 'owner' ? DEFAULT_NODE_HEIGHT : EMPTY_GROUP_HEIGHT,
  }))
  const grid = flowIntoGrid(sizes, {
    gapX: DEFAULT_NODE_SEP,
    gapY: DEFAULT_RANK_SEP,
  })
  const placements = new Map(grid.placements.map((p) => [p.id, p]))
  return nodes.map((n, index) => {
    const { w, h } = sizes[index]!
    const at = placements.get(n.id)
    return {
      ...n,
      position: { x: at?.x ?? 0, y: at?.y ?? 0 },
      width: w,
      height: h,
      style: { ...n.style, width: w, height: h },
    }
  })
}

/** Separate the placed department boxes from the loose top-level nodes. */
function splitPositioned(
  positioned: ReadonlyMap<string, Node>,
  plan: HierarchyPlan,
): { groupResults: GroupResult[]; topLevelLeaves: Map<string, Node> } {
  const groupResults: GroupResult[] = []
  const topLevelLeaves = new Map<string, Node>()
  for (const [id, node] of positioned) {
    if (!plan.departments.has(id)) {
      topLevelLeaves.set(id, node)
      continue
    }
    groupResults.push({
      node,
      childrenRelative: plan.departments.get(id)?.childrenRelative ?? [],
      groupWidth: node.width ?? 0,
      groupHeight: node.height ?? 0,
    })
  }
  return { groupResults, topLevelLeaves }
}

/**
 * Apply dagre hierarchical layout to React Flow nodes and edges.
 *
 * Returns a new array of nodes with `position` set; edges are unchanged.
 * Units are sized from the inside out (teams, then the departments holding
 * them), each laid out in its own direction and its own separations, and the
 * top-level frame then arranges the resulting boxes top-to-bottom.
 */
/** The layout's own inputs, with every option resolved to a value. */
function resolveOptions(options: LayoutOptions): {
  params: DagreParams
  chrome: DeptChromePrefs
  density: Density | undefined
} {
  const {
    direction = 'TB',
    rankSep = DEFAULT_RANK_SEP,
    nodeSep = DEFAULT_NODE_SEP,
    density,
    showBudgetBar = false,
    showStatusDots = false,
    showAddAgentButton = false,
  } = options
  return {
    params: { direction, nodeSep, rankSep },
    chrome: {
      cardPadding: cardPaddingFor(density),
      showBudgetBar,
      showStatusDots,
      showAddAgentButton,
    },
    density,
  }
}

export function applyDagreLayout(
  nodes: Node[],
  edges: Edge[],
  options: LayoutOptions = {},
): Node[] {
  const { params, chrome, density } = resolveOptions(options)
  // Sized before anything measures them, so the reserve inside every department
  // box is the footprint its agent cards actually render at.
  const sizedNodes = sizeAgentNodes(nodes, density)

  if (!sizedNodes.some((n) => n.type === 'department') && edges.length === 0) {
    return layoutEmptyChart(sizedNodes)
  }

  const plan = planHierarchy({ nodes: sizedNodes, edges, params, chrome })

  const topLevelRanks = planRanks(plan.topLevelNodes, plan.topLevelEdges)
  const { groupResults: allGroupResults, topLevelLeaves } = splitPositioned(
    runDagreLayout(plan.topLevelNodes, plan.topLevelEdges, params, topLevelRanks.constraints),
    plan,
  )

  const rootGroupIds = collectRootGroupIds(sizedNodes.filter((n) => n.type === 'department'))
  const rootResult = allGroupResults.find((r) => rootGroupIds.has(r.node.id))

  placeDepartmentGrid(allGroupResults, rootGroupIds, rootResult)
  enforceVerticalGaps(allGroupResults, topLevelLeaves, rootGroupIds)
  centerOwnersOverRoot(topLevelLeaves, rootResult)
  anchorLayout(allGroupResults, topLevelLeaves, rootResult)

  return [
    ...allGroupResults.map((r) => r.node),
    ...topLevelLeaves.values(),
    ...allGroupResults.flatMap((r) => r.childrenRelative),
    ...[...plan.teams.values()].flatMap((t) => t.childrenRelative),
  ]
}
