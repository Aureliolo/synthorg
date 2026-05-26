import { Graph, layout } from '@dagrejs/dagre'
import type { Node, Edge } from '@xyflow/react'
import {
  type GroupResult,
  type LayoutDirection,
  CEO_TO_CHILD_MINLEN,
  DEFAULT_GROUP_PADDING,
  DESIRED_INTER_DEPT_GAP,
  EMPTY_GROUP_HEIGHT,
  EMPTY_GROUP_MIN_WIDTH,
  OWNER_TO_ROOT_MINLEN,
  getNodeDim,
} from './layout-shared'

interface DagreParams {
  direction: LayoutDirection
  nodeSep: number
  rankSep: number
}

/** Dynamic minlen for a cross-dept edge tagged by build-org-tree. */
function dagreEdgeOpts(edge: Edge): { minlen?: number } {
  const kind = (edge.data as { crossDeptKind?: string } | undefined)?.crossDeptKind
  if (kind === 'owner-to-root') return { minlen: OWNER_TO_ROOT_MINLEN }
  if (kind === 'ceo-to-child') return { minlen: CEO_TO_CHILD_MINLEN }
  return {}
}

/** Run dagre on the leaf nodes and return them positioned (top-left). */
export function runDagreOnLeaves(
  leafNodes: Node[],
  edges: Edge[],
  params: DagreParams,
): Map<string, Node> {
  const g = new Graph()
  g.setGraph({ rankdir: params.direction, nodesep: params.nodeSep, ranksep: params.rankSep })
  g.setDefaultEdgeLabel(() => ({}))

  for (const node of leafNodes) {
    const { w, h } = getNodeDim(node)
    g.setNode(node.id, { width: w, height: h })
  }

  for (const edge of edges) {
    if (g.hasNode(edge.source) && g.hasNode(edge.target)) {
      g.setEdge(edge.source, edge.target, dagreEdgeOpts(edge))
    }
  }

  layout(g)

  // Map positioned leaf nodes (dagre returns center coords; RF uses top-left).
  const positionedLeafMap = new Map<string, Node>()
  for (const node of leafNodes) {
    const dagreNode = g.node(node.id) as { x: number; y: number; width: number; height: number }
    positionedLeafMap.set(node.id, {
      ...node,
      position: {
        x: dagreNode.x - dagreNode.width / 2,
        y: dagreNode.y - dagreNode.height / 2,
      },
    })
  }
  return positionedLeafMap
}

/** The dept group ids flagged as the root by build-org-tree. */
export function collectRootGroupIds(groupNodes: Node[]): Set<string> {
  const rootGroupIds = new Set<string>()
  for (const group of groupNodes) {
    if ((group.data as { isRootDepartment?: boolean }).isRootDepartment) {
      rootGroupIds.add(group.id)
    }
  }
  return rootGroupIds
}

/**
 * Step 1: compute POPULATED dept group positions from child bounds
 * (dagre doesn't know about group nodes, so positions/dimensions are
 * derived after leaf layout).  Empty groups are deferred to Step 3.
 */
export function computePopulatedGroups(
  groupNodes: Node[],
  positionedLeafMap: Map<string, Node>,
  headerHeight: number,
  footerHeight: number,
): { populatedResults: GroupResult[]; emptyGroups: Node[] } {
  const populatedResults: GroupResult[] = []
  const emptyGroups: Node[] = []

  for (const group of groupNodes) {
    const children = [...positionedLeafMap.values()].filter((n) => n.parentId === group.id)
    if (children.length === 0) {
      emptyGroups.push(group)
      continue
    }

    const padding = DEFAULT_GROUP_PADDING
    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    for (const child of children) {
      const { w, h } = getNodeDim(child)
      minX = Math.min(minX, child.position.x)
      minY = Math.min(minY, child.position.y)
      maxX = Math.max(maxX, child.position.x + w)
      maxY = Math.max(maxY, child.position.y + h)
    }

    const contentWidth = maxX - minX
    const desiredWidth = Math.max(contentWidth + padding * 2, EMPTY_GROUP_MIN_WIDTH)
    const extraWidth = desiredWidth - (contentWidth + padding * 2)
    const leftPad = padding + extraWidth / 2

    const groupX = minX - leftPad
    const groupY = minY - padding - headerHeight
    const groupWidth = desiredWidth
    const groupHeight = maxY - minY + padding * 2 + headerHeight + footerHeight

    populatedResults.push({
      node: {
        ...group,
        position: { x: groupX, y: groupY },
        width: groupWidth,
        height: groupHeight,
        style: { ...group.style, width: groupWidth, height: groupHeight },
      },
      children,
      groupX,
      groupY,
      groupWidth,
      groupHeight,
    })
  }
  return { populatedResults, emptyGroups }
}

/**
 * Step 2: convert populated children to group-relative coords, so any
 * subsequent shift of the group position automatically moves the
 * children along with it -- no per-child bookkeeping needed.
 */
export function toGroupRelative(
  populatedResults: GroupResult[],
  positionedLeafMap: Map<string, Node>,
): void {
  for (const { children, groupX, groupY } of populatedResults) {
    for (const child of children) {
      positionedLeafMap.set(child.id, {
        ...child,
        position: { x: child.position.x - groupX, y: child.position.y - groupY },
      })
    }
  }
}

/**
 * Step 2.5: centre each dept lead over its reports.  Dagre lays out
 * lead + reports as siblings in a row; re-anchoring the lead to the
 * horizontal midpoint of its reports forms a clean T-junction and
 * fixes the head -> report connector routing.
 */
export function centerLeadsOverReports(
  populatedResults: GroupResult[],
  positionedLeafMap: Map<string, Node>,
): void {
  for (const groupResult of populatedResults) {
    const groupChildren = groupResult.children
      .map((c) => positionedLeafMap.get(c.id))
      .filter((c): c is Node => c !== undefined)
    const lead = groupChildren.find(
      (c) => (c.data as { isDeptLead?: boolean }).isDeptLead === true,
    )
    if (!lead) continue
    const reports = groupChildren.filter((c) => c.id !== lead.id)
    if (reports.length === 0) continue
    let xMin = Infinity
    let xMax = -Infinity
    for (const report of reports) {
      const { w } = getNodeDim(report)
      xMin = Math.min(xMin, report.position.x)
      xMax = Math.max(xMax, report.position.x + w)
    }
    const { w: leadW } = getNodeDim(lead)
    positionedLeafMap.set(lead.id, {
      ...lead,
      position: { x: (xMin + xMax) / 2 - leadW / 2, y: lead.position.y },
    })
  }
}

/**
 * Step 3: place EMPTY dept groups into the non-root row (done BEFORE
 * the centering pass so empty depts are part of the cluster that gets
 * centered, not appended asymmetrically afterwards).
 */
export function placeEmptyGroups(
  emptyGroups: Node[],
  populatedResults: GroupResult[],
  rootGroupIds: Set<string>,
  headerHeight: number,
  rootPopulated: GroupResult | undefined,
): GroupResult[] {
  const emptyResults: GroupResult[] = []
  const populatedNonRoot = populatedResults.filter((r) => !rootGroupIds.has(r.node.id))

  let nonRootRowY = 0
  let nonRootRowRightEdge = 0
  if (populatedNonRoot.length > 0) {
    nonRootRowY = Math.min(...populatedNonRoot.map((r) => r.node.position.y))
    nonRootRowRightEdge = Math.max(
      ...populatedNonRoot.map((r) => r.node.position.x + r.groupWidth),
    )
  } else if (rootPopulated) {
    // No populated non-root depts -- fall back to placing empty depts
    // below the root dept (edge case: org with only a CEO).
    nonRootRowY =
      rootPopulated.node.position.y + rootPopulated.groupHeight + headerHeight + DEFAULT_GROUP_PADDING
    nonRootRowRightEdge = rootPopulated.node.position.x + rootPopulated.groupWidth
  }

  for (const group of emptyGroups) {
    const isRoot = rootGroupIds.has(group.id)
    let groupX: number
    let groupY: number
    if (isRoot) {
      // Empty ROOT dept (no CEO, very unusual).  Anchor above row.
      groupX = nonRootRowRightEdge - EMPTY_GROUP_MIN_WIDTH
      groupY = nonRootRowY - EMPTY_GROUP_HEIGHT - DEFAULT_GROUP_PADDING * 2
    } else {
      groupX = nonRootRowRightEdge + DEFAULT_GROUP_PADDING
      groupY = nonRootRowY
      nonRootRowRightEdge = groupX + EMPTY_GROUP_MIN_WIDTH
    }
    emptyResults.push({
      node: {
        ...group,
        position: { x: groupX, y: groupY },
        width: EMPTY_GROUP_MIN_WIDTH,
        height: EMPTY_GROUP_HEIGHT,
        style: { ...group.style, width: EMPTY_GROUP_MIN_WIDTH, height: EMPTY_GROUP_HEIGHT },
      },
      children: [],
      groupX,
      groupY,
      groupWidth: EMPTY_GROUP_MIN_WIDTH,
      groupHeight: EMPTY_GROUP_HEIGHT,
    })
  }
  return emptyResults
}

/**
 * Step 4: centre the MEDIAN non-root dept under root.  For odd counts
 * the visual middle is the median-x dept (siblings have different
 * widths so the bbox midpoint drifts off the median item's centre);
 * for even counts we average the two middle items.
 */
export function centerNonRootUnderRoot(
  allGroupResults: GroupResult[],
  rootGroupIds: Set<string>,
  rootPopulated: GroupResult | undefined,
): void {
  const nonRootResults = allGroupResults.filter((r) => !rootGroupIds.has(r.node.id))
  if (!rootPopulated || nonRootResults.length === 0) return

  const rootCenterX = rootPopulated.node.position.x + rootPopulated.groupWidth / 2
  const sortedByX = [...nonRootResults].sort(
    (a, b) =>
      a.node.position.x + a.groupWidth / 2 - (b.node.position.x + b.groupWidth / 2),
  )
  let targetCenterX: number
  if (sortedByX.length % 2 === 1) {
    const mid = sortedByX[(sortedByX.length - 1) / 2]!
    targetCenterX = mid.node.position.x + mid.groupWidth / 2
  } else {
    const left = sortedByX[sortedByX.length / 2 - 1]!
    const right = sortedByX[sortedByX.length / 2]!
    const leftCentre = left.node.position.x + left.groupWidth / 2
    const rightCentre = right.node.position.x + right.groupWidth / 2
    targetCenterX = (leftCentre + rightCentre) / 2
  }
  const deltaX = rootCenterX - targetCenterX
  if (Math.abs(deltaX) <= 0.5) return
  for (const result of nonRootResults) {
    result.node = {
      ...result.node,
      position: { x: result.node.position.x + deltaX, y: result.node.position.y },
    }
  }
}

/** Lowest-positioned owner node's bottom edge, or null if no owners. */
function computeOwnerBottom(positionedLeafMap: Map<string, Node>): number | null {
  let ownerBottomY: number | null = null
  for (const node of positionedLeafMap.values()) {
    if (node.type !== 'owner') continue
    const { h: ownerH } = getNodeDim(node)
    const bottom = node.position.y + ownerH
    if (ownerBottomY === null || bottom > ownerBottomY) ownerBottomY = bottom
  }
  return ownerBottomY
}

/** Step 4.5a: anchor the root dept below the owner row. */
function anchorRootBelowOwner(
  allGroupResults: GroupResult[],
  rootGroupIds: Set<string>,
  ownerBottomY: number | null,
): void {
  const rootResult = allGroupResults.find((r) => rootGroupIds.has(r.node.id))
  if (!rootResult || ownerBottomY === null) return
  const desiredRootTop = ownerBottomY + DESIRED_INTER_DEPT_GAP
  const deltaY = desiredRootTop - rootResult.node.position.y
  if (Math.abs(deltaY) <= 0.5) return
  rootResult.node = {
    ...rootResult.node,
    position: { x: rootResult.node.position.x, y: rootResult.node.position.y + deltaY },
  }
}

/** Step 4.5b: anchor the non-root dept row below the root. */
function anchorNonRootBelowRoot(
  allGroupResults: GroupResult[],
  rootGroupIds: Set<string>,
): void {
  const rootResult = allGroupResults.find((r) => rootGroupIds.has(r.node.id))
  if (!rootResult) return
  const rootBottom = rootResult.node.position.y + rootResult.groupHeight
  const desiredNonRootTop = rootBottom + DESIRED_INTER_DEPT_GAP
  const nonRootResultsForShift = allGroupResults.filter((r) => !rootGroupIds.has(r.node.id))
  if (nonRootResultsForShift.length === 0) return
  const currentRowTop = Math.min(...nonRootResultsForShift.map((r) => r.node.position.y))
  const deltaY = desiredNonRootTop - currentRowTop
  if (Math.abs(deltaY) <= 0.5) return
  for (const result of nonRootResultsForShift) {
    result.node = {
      ...result.node,
      position: { x: result.node.position.x, y: result.node.position.y + deltaY },
    }
  }
}

/**
 * Step 4.5: enforce constant pixel gaps by SHIFTING dept box
 * positions directly (children translate automatically because they
 * are stored in group-relative coords).  Order: root below owner,
 * then every non-root dept below the root.
 */
export function enforceVerticalGaps(
  allGroupResults: GroupResult[],
  positionedLeafMap: Map<string, Node>,
  rootGroupIds: Set<string>,
): void {
  const ownerBottomY = computeOwnerBottom(positionedLeafMap)
  anchorRootBelowOwner(allGroupResults, rootGroupIds, ownerBottomY)
  anchorNonRootBelowRoot(allGroupResults, rootGroupIds)
}

/**
 * Step 5: centre top-level owner nodes over the root dept so the
 * owner -> root-dept edge is a straight vertical drop.
 */
export function centerOwnersOverRoot(
  positionedLeafMap: Map<string, Node>,
  rootPopulated: GroupResult | undefined,
): void {
  if (!rootPopulated) return
  const rootCenterX = rootPopulated.node.position.x + rootPopulated.groupWidth / 2

  const owners = [...positionedLeafMap.values()].filter((node) => node.type === 'owner')
  if (owners.length === 0) return

  // Shift the whole owners row by one shared delta so the row centres
  // over the root while preserving the spacing between owners (per-owner
  // recentring stacked them all at the same x).
  let centerSum = 0
  for (const node of owners) {
    const { w: ownerWidth } = getNodeDim(node)
    centerSum += node.position.x + ownerWidth / 2
  }
  const ownersRowCenter = centerSum / owners.length
  const delta = rootCenterX - ownersRowCenter

  for (const node of owners) {
    positionedLeafMap.set(node.id, {
      ...node,
      position: { x: node.position.x + delta, y: node.position.y },
    })
  }
}
