import type { Node, Edge } from '@xyflow/react'
import {
  type GroupResult,
  type LayoutOptions,
  DEFAULT_NODE_HEIGHT,
  DEFAULT_NODE_SEP,
  DEFAULT_NODE_WIDTH,
  DEFAULT_RANK_SEP,
  DESIRED_INTER_DEPT_GAP_X,
  EMPTY_GROUP_HEIGHT,
  EMPTY_GROUP_MIN_WIDTH,
  cardPaddingFor,
  computeFooterHeight,
  computeHeaderHeight,
} from './layout-shared'
import { runDagreLayout } from './layout-graph'
import { collectRootGroupIds, placeEmptyGroups, planHierarchy } from './layout-groups'
import type { SizedUnit } from './layout-units'
import {
  anchorLayout,
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

/** Separate the placed department boxes from the loose top-level nodes. */
function splitPositioned(
  positioned: ReadonlyMap<string, Node>,
  departments: ReadonlyMap<string, SizedUnit>,
): { populatedResults: GroupResult[]; topLevelLeaves: Map<string, Node> } {
  const populatedResults: GroupResult[] = []
  const topLevelLeaves = new Map<string, Node>()
  for (const [id, node] of positioned) {
    const department = departments.get(id)
    if (!department) {
      topLevelLeaves.set(id, node)
      continue
    }
    populatedResults.push({
      node,
      childrenRelative: department.childrenRelative,
      groupWidth: node.width ?? 0,
      groupHeight: node.height ?? 0,
    })
  }
  return { populatedResults, topLevelLeaves }
}

/**
 * Apply dagre hierarchical layout to React Flow nodes and edges.
 *
 * Returns a new array of nodes with `position` set; edges are unchanged.
 * Units are sized from the inside out (teams, then the departments holding
 * them), each laid out in its own direction and its own separations, and the
 * top-level frame then arranges the resulting boxes top-to-bottom.
 */
export function applyDagreLayout(
  nodes: Node[],
  edges: Edge[],
  options: LayoutOptions = {},
): Node[] {
  const {
    direction = 'TB',
    rankSep = DEFAULT_RANK_SEP,
    nodeSep = DEFAULT_NODE_SEP,
    density,
  } = options
  const cardPadding = cardPaddingFor(density)

  if (!nodes.some((n) => n.type !== 'department' && n.type !== 'owner')) {
    return layoutEmptyChart(nodes)
  }

  const params = { direction, nodeSep, rankSep }
  const plan = planHierarchy({
    nodes,
    edges,
    params,
    chrome: {
      cardPadding,
      // Reserve exactly the chrome that will be rendered, so turning the
      // budget bar or status dots off leaves no dead whitespace in the card.
      headerHeight: computeHeaderHeight(options, cardPadding),
      footerHeight: computeFooterHeight(options),
    },
  })

  const { populatedResults, topLevelLeaves } = splitPositioned(
    runDagreLayout(plan.topLevelNodes, plan.topLevelEdges, params),
    plan.departments,
  )

  const rootGroupIds = collectRootGroupIds(nodes.filter((n) => n.type === 'department'))
  const rootPopulated = populatedResults.find((r) => rootGroupIds.has(r.node.id))
  const allGroupResults = [
    ...populatedResults,
    ...placeEmptyGroups(plan.emptyDepartments, populatedResults, rootGroupIds, rootPopulated),
  ]
  // placeEmptyGroups can create the root group when it has no members, so
  // re-resolve the root from the merged set before the alignment passes;
  // otherwise a freshly-created empty root is skipped by them.
  const rootResult = allGroupResults.find((r) => rootGroupIds.has(r.node.id))

  enforceHorizontalGaps(allGroupResults, rootGroupIds, DESIRED_INTER_DEPT_GAP_X)
  centerNonRootUnderRoot(allGroupResults, rootGroupIds, rootResult)
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
