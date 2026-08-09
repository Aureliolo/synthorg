import type { Node } from '@xyflow/react'
import { type GroupResult, DESIRED_INTER_DEPT_GAP, getNodeDim } from './layout-shared'

/**
 * De-overlap sibling dept boxes horizontally.
 *
 * The top-level frame separates boxes by dagre's nodesep, which is tuned for
 * cards rather than for the visible gap the chart reads by. Sweeping
 * left-to-right and shifting any box that sits closer than the target gap
 * guarantees a constant one. Contents ride along automatically: they are
 * stored relative to their box.
 */
export function enforceHorizontalGaps(
  allGroupResults: GroupResult[],
  rootGroupIds: ReadonlySet<string>,
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
      curr.node = { ...curr.node, position: { x: minLeft, y: curr.node.position.y } }
    }
  }
}

/**
 * Centre the MEDIAN non-root dept under root. For odd counts the visual middle
 * is the median-x dept (siblings have different widths, so the bbox midpoint
 * drifts off the median item's centre); for even counts the two middle items
 * are averaged.
 */
export function centerNonRootUnderRoot(
  allGroupResults: GroupResult[],
  rootGroupIds: ReadonlySet<string>,
  rootPopulated: GroupResult | undefined,
): void {
  const nonRootResults = allGroupResults.filter((r) => !rootGroupIds.has(r.node.id))
  if (!rootPopulated || nonRootResults.length === 0) return

  const rootCenterX = rootPopulated.node.position.x + rootPopulated.groupWidth / 2
  const sortedByX = [...nonRootResults].sort(
    (a, b) => a.node.position.x + a.groupWidth / 2 - (b.node.position.x + b.groupWidth / 2),
  )
  let targetCenterX: number
  if (sortedByX.length % 2 === 1) {
    const mid = sortedByX[(sortedByX.length - 1) / 2]!
    targetCenterX = mid.node.position.x + mid.groupWidth / 2
  } else {
    const left = sortedByX[sortedByX.length / 2 - 1]!
    const right = sortedByX[sortedByX.length / 2]!
    targetCenterX =
      (left.node.position.x + left.groupWidth / 2 + right.node.position.x + right.groupWidth / 2) / 2
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
function computeOwnerBottom(topLevelLeaves: ReadonlyMap<string, Node>): number | null {
  let ownerBottomY: number | null = null
  for (const node of topLevelLeaves.values()) {
    if (node.type !== 'owner') continue
    const bottom = node.position.y + getNodeDim(node).h
    if (ownerBottomY === null || bottom > ownerBottomY) ownerBottomY = bottom
  }
  return ownerBottomY
}

/** Anchor the root dept below the owner row. */
function anchorRootBelowOwner(
  rootResult: GroupResult | undefined,
  ownerBottomY: number | null,
): void {
  if (!rootResult || ownerBottomY === null) return
  const deltaY = ownerBottomY + DESIRED_INTER_DEPT_GAP - rootResult.node.position.y
  if (Math.abs(deltaY) <= 0.5) return
  rootResult.node = {
    ...rootResult.node,
    position: { x: rootResult.node.position.x, y: rootResult.node.position.y + deltaY },
  }
}

/** Anchor the non-root dept row below the root. */
function anchorNonRootBelowRoot(
  allGroupResults: GroupResult[],
  rootGroupIds: ReadonlySet<string>,
  rootResult: GroupResult | undefined,
): void {
  if (!rootResult) return
  const desiredTop = rootResult.node.position.y + rootResult.groupHeight + DESIRED_INTER_DEPT_GAP
  const nonRoot = allGroupResults.filter((r) => !rootGroupIds.has(r.node.id))
  if (nonRoot.length === 0) return
  const currentTop = Math.min(...nonRoot.map((r) => r.node.position.y))
  const deltaY = desiredTop - currentTop
  if (Math.abs(deltaY) <= 0.5) return
  for (const result of nonRoot) {
    result.node = {
      ...result.node,
      position: { x: result.node.position.x, y: result.node.position.y + deltaY },
    }
  }
}

/**
 * Enforce constant pixel gaps by SHIFTING dept box positions directly
 * (contents translate automatically because they are stored relative to their
 * box). Order: root below owner, then every non-root dept below the root.
 */
export function enforceVerticalGaps(
  allGroupResults: GroupResult[],
  topLevelLeaves: ReadonlyMap<string, Node>,
  rootGroupIds: ReadonlySet<string>,
): void {
  const rootResult = allGroupResults.find((r) => rootGroupIds.has(r.node.id))
  anchorRootBelowOwner(rootResult, computeOwnerBottom(topLevelLeaves))
  anchorNonRootBelowRoot(allGroupResults, rootGroupIds, rootResult)
}

/**
 * Centre the owner row over the root dept so the owner -> root-dept edge is a
 * straight vertical drop. The whole row moves by one shared delta, preserving
 * the spacing between owners; recentring each owner stacked them all at one x.
 */
export function centerOwnersOverRoot(
  topLevelLeaves: Map<string, Node>,
  rootPopulated: GroupResult | undefined,
): void {
  if (!rootPopulated) return
  const owners = [...topLevelLeaves.values()].filter((node) => node.type === 'owner')
  if (owners.length === 0) return

  const rootCenterX = rootPopulated.node.position.x + rootPopulated.groupWidth / 2
  let centerSum = 0
  for (const node of owners) centerSum += node.position.x + getNodeDim(node).w / 2
  const delta = rootCenterX - centerSum / owners.length

  for (const node of owners) {
    topLevelLeaves.set(node.id, {
      ...node,
      position: { x: node.position.x + delta, y: node.position.y },
    })
  }
}

/** Horizontal centre of a row of nodes, or null when the row is empty. */
function rowCenterX(nodes: readonly Node[]): number | null {
  if (nodes.length === 0) return null
  let sum = 0
  for (const node of nodes) sum += node.position.x + getNodeDim(node).w / 2
  return sum / nodes.length
}

/**
 * The x the chart is pinned by. The root department is the fixed point every
 * other pass places against, so pinning it keeps the operator's eye-line
 * still. Without a root department (an org with no CEO) the owner row is the
 * next most stable landmark, and a chart with neither falls back to its own
 * left edge.
 */
function resolveAnchorX(
  allGroupResults: readonly GroupResult[],
  topLevelLeaves: readonly Node[],
  rootResult: GroupResult | undefined,
): number {
  if (rootResult) return rootResult.node.position.x + rootResult.groupWidth / 2
  const ownersCenter = rowCenterX(topLevelLeaves.filter((n) => n.type === 'owner'))
  if (ownersCenter !== null) return ownersCenter
  return Math.min(
    ...allGroupResults.map((r) => r.node.position.x),
    ...topLevelLeaves.map((n) => n.position.x),
  )
}

/**
 * Final pass: express the whole chart in a frame anchored on its spine.
 *
 * dagre re-origins every layout on its own bounding box, so a department
 * appearing on one side translates everything on the other side too and the
 * chart appears to jump under a viewport that never moved. Pinning the root
 * department's centre to x = 0 and the top of the chart to y = 0 means growth
 * extends outward from the spine instead. Box contents ride along untouched
 * because they are stored relative to their box.
 */
export function anchorLayout(
  allGroupResults: GroupResult[],
  topLevelLeaves: Map<string, Node>,
  rootResult: GroupResult | undefined,
): void {
  const leaves = [...topLevelLeaves.values()]
  if (allGroupResults.length === 0 && leaves.length === 0) return

  const deltaX = -resolveAnchorX(allGroupResults, leaves, rootResult)
  const deltaY = -Math.min(
    ...allGroupResults.map((r) => r.node.position.y),
    ...leaves.map((n) => n.position.y),
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
  for (const node of leaves) {
    topLevelLeaves.set(node.id, {
      ...node,
      position: { x: node.position.x + deltaX, y: node.position.y + deltaY },
    })
  }
}
