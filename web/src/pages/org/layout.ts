import { Graph, layout } from '@dagrejs/dagre'
import type { Node, Edge } from '@xyflow/react'

// Only 'TB' is currently used.  The post-layout adjustment pass
// (Steps 4-5) assumes a top-to-bottom layout.  Adding 'LR' support
// would require mirroring those steps along the x-axis.
export type LayoutDirection = 'TB'

/**
 * Per-render visual preferences that affect how much space the
 * dept card chrome takes up.  Passed through from
 * `useOrgChartData` so the layout reserves exactly the space that
 * will actually be rendered -- no more "100 px of empty space when
 * the user turns toggles off" bug.
 */
export interface LayoutVisualPrefs {
  showBudgetBar?: boolean
  showStatusDots?: boolean
  showAddAgentButton?: boolean
}

export interface LayoutOptions extends LayoutVisualPrefs {
  direction?: LayoutDirection
  nodeSep?: number
  rankSep?: number
}

const DEFAULT_NODE_WIDTH = 160
const DEFAULT_NODE_HEIGHT = 80
const DEFAULT_GROUP_PADDING = 16

// Fixed header pieces on every dept card (inner padding + title row + bottom margin)
const HEADER_BASE = 48
// Added when budget bar is on (label + 1 px bar + spacing)
const HEADER_BUDGET_BAR = 26
// Added when status dots are on.  The dots are `size-2.5` (10 px) +
// `ring-2` (4 px per side) and sit on a `pt-1` (4 px) padding line,
// so the row occupies roughly 18 px of vertical space inside the
// card header.
const HEADER_STATUS_DOTS = 20
// Bottom footer chip ("+ Add agent")
const FOOTER_ADD_AGENT = 34

function computeHeaderHeight(prefs: LayoutVisualPrefs): number {
  let h = HEADER_BASE
  if (prefs.showBudgetBar) h += HEADER_BUDGET_BAR
  if (prefs.showStatusDots) h += HEADER_STATUS_DOTS
  return h
}

function computeFooterHeight(prefs: LayoutVisualPrefs): number {
  return prefs.showAddAgentButton ? FOOTER_ADD_AGENT : 0
}

const EMPTY_GROUP_MIN_WIDTH = 240
// Matches the empty-state card's min-h -- header + "No agents yet"
// icon + label + (optional) add agent chip.
const EMPTY_GROUP_HEIGHT = 180

function getNodeDim(node: Node): { w: number; h: number } {
  const w = node.measured?.width ?? (node.width as number | undefined) ?? DEFAULT_NODE_WIDTH
  const h = node.measured?.height ?? (node.height as number | undefined) ?? DEFAULT_NODE_HEIGHT
  return { w, h }
}

/**
 * Apply dagre hierarchical layout to React Flow nodes and edges.
 *
 * Returns a new array of nodes with `position` set. Edges are unchanged.
 * Group (department) nodes are excluded from dagre and sized to contain
 * their children after layout.
 */
export function applyDagreLayout(
  nodes: Node[],
  edges: Edge[],
  options: LayoutOptions = {},
): Node[] {
  const { direction = 'TB' } = options
  let { nodeSep = 60 } = options
  const { rankSep = 50 } = options

  // Dynamic header/footer sizes based on what's actually rendered.
  // When the user toggles off budget bar / status dots, the card
  // chrome shrinks and the reserved space shrinks with it -- so
  // there is no dead whitespace inside the box.
  const headerHeight = computeHeaderHeight(options)
  const footerHeight = computeFooterHeight(options)

  // Target visible gap between any two adjacent dept boxes in the
  // hierarchy.  The gap is enforced AFTER dagre by a manual shift
  // pass (see below), not by dagre's minlen -- dagre uses integer
  // ranks which quantize into 50 px jumps depending on how close
  // the header chrome size is to a rankSep boundary, causing the
  // gap to change when the user toggles status dots on/off.
  const DESIRED_INTER_DEPT_GAP = 48
  // Static minlens used only to keep dagre's ranking correct (so
  // it doesn't compact the graph into a single rank).  Actual
  // spacing comes from the post-shift pass.
  const ownerToRootMinlen = 2
  const ceoToChildMinlen = 2

  const groupNodes = nodes.filter((n) => n.type === 'department')
  const leafNodes = nodes.filter((n) => n.type !== 'department')

  if (groupNodes.length > 0) {
    nodeSep += DEFAULT_GROUP_PADDING * 2
  }

  const agentLeafNodes = leafNodes.filter((n) => n.type !== 'owner')
  if (agentLeafNodes.length === 0) {
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

  // ── Build + run dagre on leaf nodes only ─────────────────
  const g = new Graph()
  g.setGraph({ rankdir: direction, nodesep: nodeSep, ranksep: rankSep })
  g.setDefaultEdgeLabel(() => ({}))

  for (const node of leafNodes) {
    const { w, h } = getNodeDim(node)
    g.setNode(node.id, { width: w, height: h })
  }

  for (const edge of edges) {
    if (g.hasNode(edge.source) && g.hasNode(edge.target)) {
      // Cross-dept edges are tagged by build-org-tree with
      // `data.crossDeptKind` -- look up the dynamic minlen for
      // that kind (owner-to-root is shorter, ceo-to-child is
      // longer because it needs to clear more chrome).  Edges
      // without a tag are intra-dept (head→member) and keep
      // dagre's default minlen of 1.
      const kind = (edge.data as { crossDeptKind?: string } | undefined)?.crossDeptKind
      let minlen: number | undefined
      if (kind === 'owner-to-root') minlen = ownerToRootMinlen
      else if (kind === 'ceo-to-child') minlen = ceoToChildMinlen
      g.setEdge(edge.source, edge.target, minlen ? { minlen } : {})
    }
  }

  layout(g)

  // Map positioned leaf nodes (dagre returns center coords; RF uses top-left)
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

  // Identify the root department (the one flagged by build-org-tree).
  const rootGroupIds = new Set<string>()
  for (const group of groupNodes) {
    if ((group.data as { isRootDepartment?: boolean }).isRootDepartment) {
      rootGroupIds.add(group.id)
    }
  }

  // ── Step 1: compute POPULATED dept group positions ───────
  //
  // Pure from-children bounds (dagre doesn't know about group nodes,
  // so group positions/dimensions are derived after leaf layout).
  interface GroupResult {
    node: Node
    children: Node[]
    groupX: number
    groupY: number
    groupWidth: number
    groupHeight: number
  }

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
    const groupHeight =
      maxY - minY + padding * 2 + headerHeight + footerHeight

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

  // ── Step 2: convert populated children to group-relative ────
  //
  // Children are stored in group-relative coords from here on, so
  // any subsequent shift of the group position automatically moves
  // the children along with it -- no per-child bookkeeping needed.
  for (const { children, groupX, groupY } of populatedResults) {
    for (const child of children) {
      positionedLeafMap.set(child.id, {
        ...child,
        position: {
          x: child.position.x - groupX,
          y: child.position.y - groupY,
        },
      })
    }
  }

  // ── Step 2.5: centre each dept lead over its reports ──────
  //
  // Dagre lays out lead + reports as siblings in a row; without a
  // post-pass the lead lands wherever dagre's barycentric ordering
  // places it (often the leftmost slot of the row), so the
  // head -> report connector L-jogs sideways instead of forming a
  // clean T-junction above the children's bounding-box midpoint.
  // Re-anchoring the lead to the horizontal midpoint of its reports
  // fixes the visual hierarchy and the connector routing in a single
  // pass (HierarchyEdge drops from sourceX = lead.x + lead.w / 2 to
  // the midpoint between source and target).
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
      position: {
        x: (xMin + xMax) / 2 - leadW / 2,
        y: lead.position.y,
      },
    })
  }

  const rootPopulated = populatedResults.find((r) => rootGroupIds.has(r.node.id))

  // ── Step 3: place EMPTY dept groups into the non-root row ────
  //
  // Done BEFORE the centering pass so empty depts are part of the
  // cluster that gets centered, not appended asymmetrically after
  // the fact.  That way a row like [Product, Engineering, Security]
  // (where Security is empty) is treated as a single block when we
  // align it below the root dept.
  const emptyResults: GroupResult[] = []
  const populatedNonRoot = populatedResults.filter((r) => !rootGroupIds.has(r.node.id))

  // Row Y + right edge for placing empty depts come from the
  // populated non-root cluster's current dagre-provided position.
  let nonRootRowY = 0
  let nonRootRowRightEdge = 0
  if (populatedNonRoot.length > 0) {
    nonRootRowY = Math.min(...populatedNonRoot.map((r) => r.node.position.y))
    nonRootRowRightEdge = Math.max(
      ...populatedNonRoot.map((r) => r.node.position.x + r.groupWidth),
    )
  } else if (rootPopulated) {
    // No populated non-root depts -- fall back to placing empty
    // depts below the root dept (edge case: org with only a CEO).
    nonRootRowY = rootPopulated.node.position.y + rootPopulated.groupHeight + headerHeight + DEFAULT_GROUP_PADDING
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

  const allGroupResults = [...populatedResults, ...emptyResults]

  // ── Step 4: center the MEDIAN non-root dept under root ─────
  //
  // For an odd number of siblings the "visual middle" is the
  // median-x dept, not the bounding-box midpoint -- because the
  // siblings typically have different widths, the bbox midpoint
  // drifts off the median item's centre and Engineering ends up
  // slightly left/right of Executive even though it IS the middle
  // child.  Shifting by `rootCenterX - medianCenterX` puts the
  // middle dept's x exactly under the root dept.
  //
  // For even counts we average the two middle items, which is the
  // equivalent of bbox centring for a symmetric pair.
  const nonRootResults = allGroupResults.filter((r) => !rootGroupIds.has(r.node.id))
  if (rootPopulated && nonRootResults.length > 0) {
    const rootCenterX = rootPopulated.node.position.x + rootPopulated.groupWidth / 2
    const sortedByX = [...nonRootResults].sort(
      (a, b) => (a.node.position.x + a.groupWidth / 2) - (b.node.position.x + b.groupWidth / 2),
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
    if (Math.abs(deltaX) > 0.5) {
      for (const result of nonRootResults) {
        result.node = {
          ...result.node,
          position: {
            x: result.node.position.x + deltaX,
            y: result.node.position.y,
          },
        }
      }
    }
  }

  // ── Step 4.5: enforce constant vertical gaps ───────────────
  //
  // Dagre lays out agents by integer ranks * rankSep, which gives
  // us a coarse vertical spacing but cannot produce a constant
  // pixel gap between dept boxes when the box chrome (header +
  // footer) changes size.  Every time the user toggled status
  // dots / budget bar, the header grew or shrank and the visible
  // gap between Executive and its children jumped because
  // `ceil(chrome / rankSep)` quantized into 50 px increments.
  //
  // This pass sidesteps that by SHIFTING dept box positions
  // directly so the visible gaps are exactly `DESIRED_INTER_DEPT_GAP`
  // pixels regardless of chrome size.  Children of each group
  // move automatically because they are stored in group-relative
  // coordinates (see Step 2), so the whole subtree translates
  // when we change a group's `position.y`.
  //
  // Order:
  //   1. Shift the root dept so its top is `owner.bottom + GAP`.
  //   2. Shift every non-root dept so its top is `root.bottom + GAP`.

  // Find owner bottom (use the lowest-positioned owner node).
  let ownerBottomY: number | null = null
  for (const node of positionedLeafMap.values()) {
    if (node.type !== 'owner') continue
    const { h: ownerH } = getNodeDim(node)
    const bottom = node.position.y + ownerH
    if (ownerBottomY === null || bottom > ownerBottomY) ownerBottomY = bottom
  }

  // Step 4.5a: anchor root dept below owner.
  const rootResult = allGroupResults.find((r) => rootGroupIds.has(r.node.id))
  if (rootResult && ownerBottomY !== null) {
    const desiredRootTop = ownerBottomY + DESIRED_INTER_DEPT_GAP
    const deltaY = desiredRootTop - rootResult.node.position.y
    if (Math.abs(deltaY) > 0.5) {
      rootResult.node = {
        ...rootResult.node,
        position: {
          x: rootResult.node.position.x,
          y: rootResult.node.position.y + deltaY,
        },
      }
    }
  }

  // Step 4.5b: anchor non-root dept row below the root.
  if (rootResult) {
    const rootBottom = rootResult.node.position.y + rootResult.groupHeight
    const desiredNonRootTop = rootBottom + DESIRED_INTER_DEPT_GAP
    const nonRootResultsForShift = allGroupResults.filter(
      (r) => !rootGroupIds.has(r.node.id),
    )
    // Use the min of current non-root ys as the row's reference.
    // Every non-root dept shifts by the same delta so the row
    // stays visually aligned.
    if (nonRootResultsForShift.length > 0) {
      const currentRowTop = Math.min(
        ...nonRootResultsForShift.map((r) => r.node.position.y),
      )
      const deltaY = desiredNonRootTop - currentRowTop
      if (Math.abs(deltaY) > 0.5) {
        for (const result of nonRootResultsForShift) {
          result.node = {
            ...result.node,
            position: {
              x: result.node.position.x,
              y: result.node.position.y + deltaY,
            },
          }
        }
      }
    }
  }

  // ── Step 5: center top-level owner nodes over the root dept ──
  //
  // Shift horizontally so the owner sits dead-centre above the
  // root dept -- owner→root-dept edge becomes a straight vertical
  // drop with no kinks.  Uses the OWNER node's explicit width from
  // build-org-tree (220 px) rather than the 160 px default so the
  // centering math is accurate on first paint, before React Flow
  // has measured the rendered card.
  if (rootPopulated) {
    const rootCenterX = rootPopulated.node.position.x + rootPopulated.groupWidth / 2
    for (const node of positionedLeafMap.values()) {
      if (node.type !== 'owner') continue
      const { w: ownerWidth } = getNodeDim(node)
      positionedLeafMap.set(node.id, {
        ...node,
        position: {
          x: rootCenterX - ownerWidth / 2,
          y: node.position.y,
        },
      })
    }
  }

  return [...allGroupResults.map((r) => r.node), ...positionedLeafMap.values()]
}
