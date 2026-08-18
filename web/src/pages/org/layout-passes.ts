import type { Node } from '@xyflow/react'
import { flowIntoGrid } from './layout-grid'
import {
  type GroupResult,
  DESIRED_INTER_DEPT_GAP,
  DESIRED_INTER_DEPT_GAP_X,
  getNodeDim,
} from './layout-shared'

/**
 * Arrange the departments hanging off the root as a block rather than a row.
 *
 * A single row costs width per department without bound while the height stays
 * fixed, so the chart runs off the canvas sideways with most of the viewport
 * still empty: six departments measure 2700 px across against 654 px tall.
 * Wrapping into a block spends both axes, and the whole block is centred on the
 * root so the spine stays straight.
 *
 * The order handed to the grid is the order they arrive in, which is the
 * operator's own: dagre's order constraints pinned it to emission order, and
 * that is what the reorder endpoints persist.
 *
 * Contents ride along automatically, being stored relative to their box. The
 * block keeps the top dagre gave it rather than resetting to the origin: with a
 * root department `anchorNonRootBelowRoot` shifts the whole block into place
 * afterwards, but an org with no CEO has no root to anchor against and dagre's
 * own rank is then the only thing holding the departments below the owner row.
 */
export function placeDepartmentGrid(
  allGroupResults: GroupResult[],
  rootGroupIds: ReadonlySet<string>,
  rootResult: GroupResult | undefined,
): void {
  const nonRoot = allGroupResults
    .filter((r) => !rootGroupIds.has(r.node.id))
    .sort((a, b) => a.node.position.x - b.node.position.x)
  if (nonRoot.length === 0) return

  const grid = flowIntoGrid(
    nonRoot.map((r) => ({ id: r.node.id, w: r.groupWidth, h: r.groupHeight })),
    { gapX: DESIRED_INTER_DEPT_GAP_X, gapY: DESIRED_INTER_DEPT_GAP },
  )
  const anchorX = rootResult
    ? rootResult.node.position.x + rootResult.groupWidth / 2
    : nonRoot[0]!.node.position.x + grid.width / 2
  const left = anchorX - grid.width / 2
  const top = Math.min(...nonRoot.map((r) => r.node.position.y))

  const byId = new Map(nonRoot.map((r) => [r.node.id, r]))
  for (const placement of grid.placements) {
    const result = byId.get(placement.id)!
    result.node = {
      ...result.node,
      position: { x: left + placement.x, y: top + placement.y },
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
