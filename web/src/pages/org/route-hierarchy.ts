/**
 * Routing the reporting lines around the cards they have to get past.
 *
 * A React Flow edge component knows its own two endpoints and nothing else, so
 * it cannot see that its target sits on the second row of a block and that a
 * straight drop would cut through the first. This module answers that from the
 * placed geometry: it groups every edge leaving one source, works out the
 * corridors between the rows of its targets, and hands each edge the two or
 * three coordinates its own path needs.
 *
 * Derived from the laid-out nodes rather than passed out of the layout, so it
 * holds for departments under the root and for agents under a department lead
 * alike, at whatever depth, and stays correct if the arrangement changes.
 */

import type { Edge, Node } from '@xyflow/react'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getNodeDim } from './layout-shared'

const log = createLogger('route-hierarchy')

/** What one hierarchy edge needs to know beyond its own endpoints. */
export interface HierarchyRouting extends Record<string, unknown> {
  /** The corridor every edge from this source drops into first. */
  trunkY: number
  /** The corridor serving this edge's target row; equals `trunkY` on the first. */
  busY: number
  /** Where to descend past the rows above; absent when the target is on the first. */
  riserX?: number
}

/** Clearance kept between a riser and the cards it passes. */
const RISER_CLEARANCE = 12

interface Box {
  readonly left: number
  readonly right: number
  readonly top: number
  readonly bottom: number
  readonly centreX: number
}

/**
 * Absolute box of every node, resolving positions stored relative to a parent.
 *
 * The `seen` set bounds the climb: `parentId` comes from server data, and a
 * cycle in it would otherwise spin with the canvas already mounted. Both ways of
 * ending the climb early leave the box measured from a partial offset, so the
 * connectors into that node are drawn somewhere it is not; the routing still has
 * to produce something, so it reports rather than throwing.
 */
function absoluteBoxes(nodes: readonly Node[]): Map<string, Box> {
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const boxes = new Map<string, Box>()
  for (const node of nodes) {
    let x = node.position.x
    let y = node.position.y
    let ancestorId = node.parentId
    const seen = new Set<string>([node.id])
    while (ancestorId !== undefined) {
      if (seen.has(ancestorId)) {
        // The ids are built from operator-authored department and agent names.
        log.warn('parent cycle in the org tree, routing from a partial offset:',
          sanitizeForLog(ancestorId))
        break
      }
      seen.add(ancestorId)
      const ancestor = byId.get(ancestorId)
      if (ancestor === undefined) {
        log.warn('node names a parent the tree does not contain:',
          sanitizeForLog(ancestorId))
        break
      }
      x += ancestor.position.x
      y += ancestor.position.y
      ancestorId = ancestor.parentId
    }
    const { w, h } = getNodeDim(node)
    boxes.set(node.id, { left: x, right: x + w, top: y, bottom: y + h, centreX: x + w / 2 })
  }
  return boxes
}

/**
 * The target boxes of one source, grouped into rows and ordered top down.
 *
 * A row is a set of boxes that overlap vertically, not a set that agrees on
 * `top`. Siblings of unequal height sit side by side with tops that differ, as
 * does a card nested in a group beside one that is not, and bucketing those on
 * `top` invents a second row inside the first. The bus for that phantom row is
 * then drawn midway between two spans that overlap, which is a y inside the very
 * cards it was meant to clear.
 *
 * Sweeping by top and carrying a running bottom leaves the rows disjoint by
 * construction, so every corridor derived from them is a real gap.
 */
function rowsOfTargets(targets: readonly Box[]): Box[][] {
  const sorted = [...targets].sort((a, b) => a.top - b.top)
  const rows: Box[][] = []
  let sweptBottom = Number.NEGATIVE_INFINITY
  for (const box of sorted) {
    const current = rows.at(-1)
    if (current !== undefined && box.top < sweptBottom) {
      current.push(box)
      sweptBottom = Math.max(sweptBottom, box.bottom)
      continue
    }
    rows.push([box])
    sweptBottom = box.bottom
  }
  return rows.map((row) => [...row].sort((a, b) => a.left - b.left))
}

/**
 * The vertical extent of a row.
 *
 * Read across every box rather than off the first one: rows are ordered left to
 * right for the riser search, so the leftmost card is not the topmost and its
 * own edges say nothing about where the row as a whole begins or ends.
 */
function rowTop(row: readonly Box[]): number {
  return Math.min(...row.map((box) => box.top))
}

function rowBottom(row: readonly Box[]): number {
  return Math.max(...row.map((box) => box.bottom))
}

/** Midway between two edges, which is where a connector's corridor belongs. */
function corridorBetween(above: number, below: number): number {
  return (above + below) / 2
}

/**
 * Candidate x values for a riser: every gap between cards, plus either flank.
 *
 * A finite candidate list rather than interval arithmetic, because in a block the
 * answer is always one of these and the flanks guarantee the list is never empty.
 */
function riserCandidates(row: readonly Box[]): number[] {
  const candidates = [
    row[0]!.left - RISER_CLEARANCE,
    row.at(-1)!.right + RISER_CLEARANCE,
  ]
  for (let index = 1; index < row.length; index++) {
    candidates.push(corridorBetween(row[index - 1]!.right, row[index]!.left))
  }
  return candidates
}

/** True when `x` clears every card in the rows a riser has to pass. */
function clearsRows(x: number, rowsAbove: readonly (readonly Box[])[]): boolean {
  return rowsAbove.every((row) =>
    row.every((box) => x <= box.left - RISER_CLEARANCE || x >= box.right + RISER_CLEARANCE),
  )
}

/**
 * Where to descend past the rows above the target's own.
 *
 * The x nearest the source, out of the corridors that clear every row in the
 * way. In a block those corridors are the column gutters, so the line drops
 * between cards rather than across them.
 */
function resolveRiserX(rowsAbove: readonly (readonly Box[])[], sourceCentreX: number): number {
  const candidates = rowsAbove
    .flatMap(riserCandidates)
    .filter((x) => clearsRows(x, rowsAbove))
  if (candidates.length === 0) {
    return Math.min(...rowsAbove.flat().map((box) => box.left)) - RISER_CLEARANCE
  }
  return candidates.reduce((best, x) =>
    Math.abs(x - sourceCentreX) < Math.abs(best - sourceCentreX) ? x : best,
  )
}

/** The routing for each target of one source, keyed by target id. */
function routeFromSource(source: Box, targets: Map<string, Box>): Map<string, HierarchyRouting> {
  // Deduplicated, because two targets inside one container share its routing
  // unit and the same box listed twice would widen its own row by nothing.
  const rows = rowsOfTargets([...new Set(targets.values())])
  const trunkY = corridorBetween(source.bottom, rowTop(rows[0]!))
  const busY = rows.map((row, index) =>
    index === 0 ? trunkY : corridorBetween(rowBottom(rows[index - 1]!), rowTop(row)),
  )
  const riserX = rows.map((_, index) =>
    index === 0 ? undefined : resolveRiserX(rows.slice(0, index), source.centreX),
  )

  const rowOf = new Map<Box, number>()
  rows.forEach((row, index) => row.forEach((box) => rowOf.set(box, index)))

  const routing = new Map<string, HierarchyRouting>()
  for (const [targetId, box] of targets) {
    const index = rowOf.get(box) ?? 0
    const at = riserX[index]
    routing.set(targetId, {
      trunkY,
      busY: busY[index]!,
      ...(at === undefined ? {} : { riserX: at }),
    })
  }
  return routing
}

/** Each node's parent, for resolving what a connector has to get past. */
function parentIds(nodes: readonly Node[]): Map<string, string> {
  const parents = new Map<string, string>()
  for (const node of nodes) {
    if (node.parentId !== undefined) parents.set(node.id, node.parentId)
  }
  return parents
}

/**
 * Every container `id` sits in, plus `id` itself.
 *
 * The `seen` guard bounds the climb for the same reason `absoluteBoxes` carries
 * one: `parentId` is server data and a cycle in it must not spin the canvas.
 */
function ancestryOf(id: string, parentOf: ReadonlyMap<string, string>): Set<string> {
  const chain = new Set<string>([id])
  let current = parentOf.get(id)
  while (current !== undefined && !chain.has(current)) {
    chain.add(current)
    current = parentOf.get(current)
  }
  return chain
}

/**
 * The node whose box a connector from `sourceId` into `targetId` must clear.
 *
 * Not the target's own card. A card nested in a group is reached by a line that
 * has to get past the whole group, and a group is taller than the card it is
 * being entered for: a team box also holds that lead's reports, so it extends
 * well below the lead. Routing on the lead cards alone puts the second row's bus
 * midway between two leads, which is a y inside the first row's team box, and
 * the line crosses the report cards in it.
 *
 * So the routing unit is the outermost container that still sits below whatever
 * the source and target have in common: climb from the target until the next
 * step up would enter something the source is already inside. For siblings that
 * stops immediately and the unit is the target's own card, which is why the same
 * rule serves departments under the root and agents under a lead alike.
 */
function routingUnitId(
  targetId: string,
  sourceAncestry: ReadonlySet<string>,
  parentOf: ReadonlyMap<string, string>,
): string {
  const seen = new Set<string>([targetId])
  let unit = targetId
  let parent = parentOf.get(unit)
  while (parent !== undefined && !sourceAncestry.has(parent) && !seen.has(parent)) {
    seen.add(parent)
    unit = parent
    parent = parentOf.get(unit)
  }
  return unit
}

/**
 * Group the routable edges by source, keeping the box each connector must clear.
 *
 * The box is the target's routing unit rather than the target itself, so the
 * corridors are derived from what is actually in the way.
 */
function targetsBySource(
  edges: readonly Edge[],
  boxes: ReadonlyMap<string, Box>,
  parentOf: ReadonlyMap<string, string>,
): Map<string, Map<string, Box>> {
  const bySource = new Map<string, Map<string, Box>>()
  const ancestryFor = ancestryCache(parentOf)
  for (const edge of edges) {
    if (!isRoutable(edge, boxes)) continue
    const unit = boxes.get(routingUnitId(edge.target, ancestryFor(edge.source), parentOf))
    if (unit === undefined) continue
    const targets = bySource.get(edge.source) ?? new Map<string, Box>()
    targets.set(edge.target, unit)
    bySource.set(edge.source, targets)
  }
  return bySource
}

/** An edge this module plans, with both ends placed. */
function isRoutable(edge: Edge, boxes: ReadonlyMap<string, Box>): boolean {
  return (
    edge.hidden !== true
    && edge.type === 'hierarchy'
    && boxes.has(edge.source)
    && boxes.has(edge.target)
  )
}

/**
 * `ancestryOf`, memoised per source.
 *
 * Every edge leaving one source asks the same question, and a source at the top
 * of a deep org answers it by walking the whole chain each time.
 */
function ancestryCache(
  parentOf: ReadonlyMap<string, string>,
): (id: string) => ReadonlySet<string> {
  const cache = new Map<string, Set<string>>()
  return (id: string) => {
    let found = cache.get(id)
    if (found === undefined) {
      found = ancestryOf(id, parentOf)
      cache.set(id, found)
    }
    return found
  }
}

/**
 * Every edge's corridors, by source then target.
 *
 * Nested rather than a joined `source|target` key: any separator that can appear
 * in an id makes ('a', 'bc') and ('ab', 'c') the same entry, so one edge would
 * receive another's plan. `targetsBySource` and `liftEdges` avoid a joined key
 * for the same reason.
 */
export type HierarchyRoutingPlan = ReadonlyMap<string, ReadonlyMap<string, HierarchyRouting>>

/**
 * Work out the corridors every hierarchy edge should follow.
 *
 * Edges leaving the same source share those corridors by construction, which is
 * what makes a set of siblings read as one trunk with a bus per row instead of
 * as one independent elbow each.
 *
 * Separate from applying the result because the two cost different things and
 * change at different rates: this reads the placed geometry and searches for a
 * clear riser per row, and that geometry only moves when the org's structure
 * does, while the edges are rebuilt on every live status frame.
 */
export function hierarchyRoutingPlan(
  nodes: readonly Node[],
  edges: readonly Edge[],
): HierarchyRoutingPlan {
  const boxes = absoluteBoxes(nodes)
  const bySource = targetsBySource(edges, boxes, parentIds(nodes))
  const routing = new Map<string, ReadonlyMap<string, HierarchyRouting>>()
  for (const [sourceId, targets] of bySource) {
    const source = boxes.get(sourceId)
    if (source === undefined) continue
    routing.set(sourceId, routeFromSource(source, targets))
  }
  return routing
}

/**
 * Hand each edge the plan for its own endpoints.
 *
 * An edge the plan does not cover is returned untouched, so it falls back to the
 * elbow `HierarchyEdge` derives from its endpoints alone.
 */
export function applyHierarchyRouting(
  edges: readonly Edge[],
  plan: HierarchyRoutingPlan,
): Edge[] {
  return edges.map((edge) => {
    const routing = plan.get(edge.source)?.get(edge.target)
    return routing === undefined ? edge : { ...edge, data: { ...edge.data, ...routing } }
  })
}
