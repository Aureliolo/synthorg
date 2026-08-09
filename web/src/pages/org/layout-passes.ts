import type { Node } from '@xyflow/react'
import type { ClusterPlan } from './layout-clusters'
import {
  type ClusterDirection,
  type GroupResult,
  DESIRED_INTER_DEPT_GAP,
  getNodeDim,
} from './layout-shared'

/** Midpoint of a set of nodes along the axis across the cluster's flow. */
function crossAxisMidpoint(nodes: readonly Node[], direction: ClusterDirection): number {
  let low = Infinity
  let high = -Infinity
  for (const node of nodes) {
    const { w, h } = getNodeDim(node)
    const start = direction === 'LR' ? node.position.y : node.position.x
    const extent = direction === 'LR' ? h : w
    low = Math.min(low, start)
    high = Math.max(high, start + extent)
  }
  return (low + high) / 2
}

/**
 * Step 0.5 (runs before Step 1): centre each dept lead across its reports.
 *
 * Dagre balances a parent between its children rather than centring it
 * exactly, so the lead sits slightly off the midpoint of the reports sharing
 * its box and the head-to-report connectors fan instead of forming a clean
 * T-junction.  Re-anchoring the lead across the flow fixes that: on x for a
 * top-to-bottom department, on y for a left-to-right one, where the lead sits
 * beside its reports rather than above them.
 *
 * Runs on absolute coords BEFORE the box bounds are computed, so the box
 * reflects the centred (tight) layout rather than dagre's spread-out one.
 */
export function centerLeadsOverReports(
  clusters: readonly ClusterPlan[],
  positionedLeafMap: Map<string, Node>,
): void {
  for (const cluster of clusters) {
    const members = cluster.memberIds
      .map((id) => positionedLeafMap.get(id))
      .filter((node): node is Node => node !== undefined)
    const lead = members.find(
      (c) => (c.data as { isDeptLead?: boolean }).isDeptLead === true,
    )
    if (!lead) continue
    const reports = members.filter((c) => c.id !== lead.id)
    if (reports.length === 0) continue
    const midpoint = crossAxisMidpoint(reports, cluster.direction)
    const { w: leadW, h: leadH } = getNodeDim(lead)
    positionedLeafMap.set(lead.id, {
      ...lead,
      position:
        cluster.direction === 'LR'
          ? { x: lead.position.x, y: midpoint - leadH / 2 }
          : { x: midpoint - leadW / 2, y: lead.position.y },
    })
  }
}

/**
 * Step 3.5: de-overlap sibling dept boxes horizontally.  Dagre only
 * separates the leaf agents (by nodeSep); it has no concept of the dept
 * BOX that wraps them, so a wide multi-agent dept overlaps its narrower
 * neighbours.  Sweeping left-to-right and shifting any box that intrudes
 * on its predecessor guarantees a constant visible gap.  Children ride
 * along automatically -- they are stored in group-relative coords.
 */
export function enforceHorizontalGaps(
  allGroupResults: GroupResult[],
  rootGroupIds: Set<string>,
  gap: number,
): void {
  const nonRoot = allGroupResults.filter((r) => !rootGroupIds.has(r.node.id))
  if (nonRoot.length < 2) return
  const sorted = [...nonRoot].sort((a, b) => a.node.position.x - b.node.position.x)
  for (let i = 1; i < sorted.length; i++) {
    const prev = sorted[i - 1]!
    const curr = sorted[i]!
    const minLeft = prev.node.position.x + prev.groupWidth + gap
    if (curr.node.position.x < minLeft) {
      curr.node = {
        ...curr.node,
        position: { x: minLeft, y: curr.node.position.y },
      }
    }
  }
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

/** Ids whose coordinates are stored relative to a group rather than the canvas. */
function collectRelativeIds(allGroupResults: readonly GroupResult[]): Set<string> {
  const ids = new Set<string>()
  for (const result of allGroupResults) {
    for (const child of result.children) ids.add(child.id)
  }
  return ids
}

/** Horizontal centre of a row of nodes, or null when the row is empty. */
function rowCenterX(nodes: readonly Node[]): number | null {
  if (nodes.length === 0) return null
  let sum = 0
  for (const node of nodes) {
    const { w } = getNodeDim(node)
    sum += node.position.x + w / 2
  }
  return sum / nodes.length
}

/**
 * The x the chart is pinned by.  The root department is the fixed point every
 * other pass places against, so pinning it keeps the operator's eye-line still.
 * Without a root department (an org with no CEO) the owner row is the next
 * most stable landmark, and a chart with neither falls back to its own left
 * edge.
 */
function resolveAnchorX(
  allGroupResults: readonly GroupResult[],
  absoluteLeaves: readonly Node[],
  rootResult: GroupResult | undefined,
): number {
  if (rootResult) return rootResult.node.position.x + rootResult.groupWidth / 2
  const ownersCenter = rowCenterX(absoluteLeaves.filter((n) => n.type === 'owner'))
  if (ownersCenter !== null) return ownersCenter
  return Math.min(
    ...allGroupResults.map((r) => r.node.position.x),
    ...absoluteLeaves.map((n) => n.position.x),
  )
}

/**
 * Final pass: express the whole chart in a frame anchored on its spine.
 *
 * dagre re-origins every layout on its own bounding box, so a department
 * appearing on one side translates everything on the other side too and the
 * chart appears to jump under a viewport that never moved.  Pinning the root
 * department's centre to x = 0 and the top of the chart to y = 0 means growth
 * extends outward from the spine instead.  Group children ride along untouched
 * because they are already stored in group-relative coordinates.
 */
export function anchorLayout(
  allGroupResults: GroupResult[],
  positionedLeafMap: Map<string, Node>,
  rootResult: GroupResult | undefined,
): void {
  const relativeIds = collectRelativeIds(allGroupResults)
  const absoluteLeaves = [...positionedLeafMap.values()].filter(
    (node) => !relativeIds.has(node.id),
  )
  if (allGroupResults.length === 0 && absoluteLeaves.length === 0) return

  const deltaX = -resolveAnchorX(allGroupResults, absoluteLeaves, rootResult)
  const deltaY = -Math.min(
    ...allGroupResults.map((r) => r.node.position.y),
    ...absoluteLeaves.map((n) => n.position.y),
  )
  if (Math.abs(deltaX) <= 0.5 && Math.abs(deltaY) <= 0.5) return

  for (const result of allGroupResults) {
    result.node = {
      ...result.node,
      position: {
        x: result.node.position.x + deltaX,
        y: result.node.position.y + deltaY,
      },
    }
  }
  for (const node of absoluteLeaves) {
    positionedLeafMap.set(node.id, {
      ...node,
      position: { x: node.position.x + deltaX, y: node.position.y + deltaY },
    })
  }
}
