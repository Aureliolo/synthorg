import type { Node, Edge } from '@xyflow/react'
import {
  type GroupResult,
  type LayoutOptions,
  DEFAULT_GROUP_PADDING,
  DEFAULT_NODE_HEIGHT,
  DEFAULT_NODE_WIDTH,
  DESIRED_INTER_DEPT_GAP_X,
  EMPTY_GROUP_HEIGHT,
  EMPTY_GROUP_MIN_WIDTH,
  computeFooterHeight,
  computeHeaderHeight,
} from './layout-shared'
import { deriveLayoutModel } from './layout-clusters'
import { runDagreLayout } from './layout-graph'
import {
  collectRootGroupIds,
  computePopulatedGroups,
  placeEmptyGroups,
  toGroupRelative,
} from './layout-groups'
import { planTeams } from './layout-teams'
import {
  anchorLayout,
  centerLeadsOverReports,
  centerNonRootUnderRoot,
  centerOwnersOverRoot,
  enforceHorizontalGaps,
  enforceVerticalGaps,
} from './layout-passes'

export type { LayoutOptions } from './layout-shared'

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
 * Returns a new array of nodes with `position` set; edges are unchanged.
 * Each populated department is a dagre cluster laid out in its own direction,
 * teams are folded into single sized boxes beforehand, and every card is then
 * sized around its contents and shifted into the constant gaps the chart
 * reads by.
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

  if (groupNodes.length > 0) {
    nodeSep += DEFAULT_GROUP_PADDING * 2
  }

  if (!nodes.some((n) => n.type !== 'department' && n.type !== 'owner')) {
    return layoutEmptyChart(nodes)
  }

  const teams = planTeams(nodes, edges, { direction, nodeSep, rankSep })
  const model = deriveLayoutModel({
    groupNodes,
    allNodes: nodes,
    leafNodes: teams.leafNodes,
    edges: teams.edges,
    nodeSep,
  })
  const positionedLeafMap = runDagreLayout(teams.leafNodes, teams.edges, model, {
    direction,
    nodeSep,
    rankSep,
  })
  const rootGroupIds = collectRootGroupIds(groupNodes)

  // Centre each lead across its in-box reports on the raw dagre coords,
  // BEFORE box bounds are derived, so each dept card wraps the tight
  // (centred) layout instead of dagre's balanced one.
  centerLeadsOverReports(model.clusters, positionedLeafMap)

  const { populatedResults, emptyGroups } = computePopulatedGroups(
    groupNodes,
    positionedLeafMap,
    headerHeight,
    footerHeight,
  )
  toGroupRelative(populatedResults, positionedLeafMap)

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

  // De-overlap sibling dept boxes horizontally before centring the row
  // under root (dagre separates only the leaf agents, not the boxes).
  enforceHorizontalGaps(allGroupResults, rootGroupIds, DESIRED_INTER_DEPT_GAP_X)
  centerNonRootUnderRoot(allGroupResults, rootGroupIds, rootResult)
  enforceVerticalGaps(allGroupResults, positionedLeafMap, rootGroupIds)
  centerOwnersOverRoot(positionedLeafMap, rootResult)
  anchorLayout(allGroupResults, positionedLeafMap, rootResult)

  return [
    ...allGroupResults.map((r) => r.node),
    ...positionedLeafMap.values(),
    ...teams.memberNodes,
  ]
}
