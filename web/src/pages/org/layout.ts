import type { Node, Edge } from '@xyflow/react'
import {
  type GroupResult,
  type LayoutOptions,
  DEFAULT_GROUP_PADDING,
  DEFAULT_NODE_HEIGHT,
  DEFAULT_NODE_WIDTH,
  EMPTY_GROUP_HEIGHT,
  EMPTY_GROUP_MIN_WIDTH,
  computeFooterHeight,
  computeHeaderHeight,
} from './layout-shared'
import {
  centerLeadsOverReports,
  centerNonRootUnderRoot,
  centerOwnersOverRoot,
  collectRootGroupIds,
  computePopulatedGroups,
  enforceVerticalGaps,
  placeEmptyGroups,
  runDagreOnLeaves,
  toGroupRelative,
} from './layout-internals'

export type {
  LayoutDirection,
  LayoutOptions,
  LayoutVisualPrefs,
} from './layout-shared'

/** Grid fallback when there are no agent leaf nodes to lay out. */
function layoutEmptyChart(nodes: Node[]): Node[] {
  return nodes.map((n, i) => {
    const major = i % 3
    const minor = Math.floor(i / 3)
    const x = major * 260
    const y = minor * 180
    const w = n.type === 'owner' ? DEFAULT_NODE_WIDTH : EMPTY_GROUP_MIN_WIDTH
    const h = n.type === 'owner' ? DEFAULT_NODE_HEIGHT : EMPTY_GROUP_HEIGHT
    return {
      ...n,
      position: { x, y },
      width: w,
      height: h,
      style: { ...n.style, width: w, height: h },
    }
  })
}

/**
 * Apply dagre hierarchical layout to React Flow nodes and edges.
 *
 * Returns a new array of nodes with `position` set. Edges are
 * unchanged.  Group (department) nodes are excluded from dagre and
 * sized to contain their children after layout.
 */
export function applyDagreLayout(
  nodes: Node[],
  edges: Edge[],
  options: LayoutOptions = {},
): Node[] {
  const { direction = 'TB', rankSep = 50 } = options
  let { nodeSep = 60 } = options

  // Dynamic header/footer sizes based on what's actually rendered, so
  // toggling off budget bar / status dots shrinks the reserved space
  // and leaves no dead whitespace inside the box.
  const headerHeight = computeHeaderHeight(options)
  const footerHeight = computeFooterHeight(options)

  const groupNodes = nodes.filter((n) => n.type === 'department')
  const leafNodes = nodes.filter((n) => n.type !== 'department')

  if (groupNodes.length > 0) {
    nodeSep += DEFAULT_GROUP_PADDING * 2
  }

  const agentLeafNodes = leafNodes.filter((n) => n.type !== 'owner')
  if (agentLeafNodes.length === 0) {
    return layoutEmptyChart(nodes)
  }

  const positionedLeafMap = runDagreOnLeaves(leafNodes, edges, { direction, nodeSep, rankSep })
  const rootGroupIds = collectRootGroupIds(groupNodes)

  const { populatedResults, emptyGroups } = computePopulatedGroups(
    groupNodes,
    positionedLeafMap,
    headerHeight,
    footerHeight,
  )
  toGroupRelative(populatedResults, positionedLeafMap)
  centerLeadsOverReports(populatedResults, positionedLeafMap)

  const rootPopulated: GroupResult | undefined = populatedResults.find((r) =>
    rootGroupIds.has(r.node.id),
  )
  const emptyResults = placeEmptyGroups(
    emptyGroups,
    populatedResults,
    rootGroupIds,
    headerHeight,
    rootPopulated,
  )
  const allGroupResults = [...populatedResults, ...emptyResults]

  // placeEmptyGroups can create the root group when it has no members, so
  // re-resolve the root from the merged set before the alignment passes;
  // otherwise a freshly-created empty root is skipped by them.
  const rootResult: GroupResult | undefined = allGroupResults.find((r) =>
    rootGroupIds.has(r.node.id),
  )

  centerNonRootUnderRoot(allGroupResults, rootGroupIds, rootResult)
  enforceVerticalGaps(allGroupResults, positionedLeafMap, rootGroupIds)
  centerOwnersOverRoot(positionedLeafMap, rootResult)

  return [...allGroupResults.map((r) => r.node), ...positionedLeafMap.values()]
}
