import type { Node } from '@xyflow/react'
import {
  type GroupResult,
  DEFAULT_GROUP_PADDING,
  EMPTY_GROUP_HEIGHT,
  EMPTY_GROUP_MIN_WIDTH,
  POPULATED_GROUP_MIN_WIDTH,
  getNodeDim,
} from './layout-shared'

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
    const desiredWidth = Math.max(contentWidth + padding * 2, POPULATED_GROUP_MIN_WIDTH)
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
