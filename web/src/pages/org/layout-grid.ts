/**
 * Flowing a set of pre-sized boxes into a block instead of a single line.
 *
 * A row of siblings costs width without bound while a column costs height
 * without bound, and a chart is read on a screen that is neither. Wrapping the
 * siblings into a block spends both, so the footprint grows as the square root
 * of the count rather than linearly in one direction.
 */

/** A box to place, already sized by whatever laid its contents out. */
export interface GridBox {
  readonly id: string
  readonly w: number
  readonly h: number
}

/** Where one box ends up, measured from the block's own top-left corner. */
export interface GridPlacement {
  readonly id: string
  readonly x: number
  readonly y: number
}

export interface GridGaps {
  readonly gapX: number
  readonly gapY: number
}

/** The placed block: its members and the footprint they occupy together. */
export interface GridResult {
  readonly placements: GridPlacement[]
  readonly width: number
  readonly height: number
  readonly columnCount: number
  readonly rowCount: number
}

/**
 * How many columns a block of `count` boxes reads best in.
 *
 * The square root is the whole rule: it is the column count that makes the
 * block as close to square as an integer division allows, which is what keeps a
 * department row from running off the canvas and a four-agent department from
 * being four cards wide. Four boxes therefore land as two and two.
 */
export function gridColumnCount(count: number): number {
  if (count <= 1) return Math.max(count, 0)
  return Math.min(count, Math.ceil(Math.sqrt(count)))
}

/** Widest box in each column, so columns line up down the whole block. */
function columnWidths(boxes: readonly GridBox[], columnCount: number): number[] {
  const widths = new Array<number>(columnCount).fill(0)
  boxes.forEach((box, index) => {
    const column = index % columnCount
    widths[column] = Math.max(widths[column]!, box.w)
  })
  return widths
}

/** Tallest box in each row, so a tall card cannot overlap the row below. */
function rowHeights(boxes: readonly GridBox[], columnCount: number): number[] {
  const heights = new Array<number>(Math.ceil(boxes.length / columnCount)).fill(0)
  boxes.forEach((box, index) => {
    const row = Math.floor(index / columnCount)
    heights[row] = Math.max(heights[row]!, box.h)
  })
  return heights
}

/** Running start offset of each track, given its extents and the gap between. */
function trackOffsets(extents: readonly number[], gap: number): number[] {
  const offsets: number[] = []
  let running = 0
  for (const extent of extents) {
    offsets.push(running)
    running += extent + gap
  }
  return offsets
}

/**
 * Place boxes into a block, filling left to right and then wrapping.
 *
 * Fill order is the order handed in, row by row, because that order is the
 * operator's own: the chart reads departments and each department's agents in
 * the order the reorder endpoints persist, and a block that reshuffled them
 * would stop agreeing with the Org Edit page.
 *
 * A box narrower than its column sits centred in it, so the connector dropping
 * into its top edge lands on the column's own centre line and the taps down a
 * row's bus stay evenly spaced whatever the boxes measure.
 */
export function flowIntoGrid(boxes: readonly GridBox[], gaps: GridGaps): GridResult {
  const columnCount = gridColumnCount(boxes.length)
  if (columnCount === 0) {
    return { placements: [], width: 0, height: 0, columnCount: 0, rowCount: 0 }
  }
  const widths = columnWidths(boxes, columnCount)
  const heights = rowHeights(boxes, columnCount)
  const columnX = trackOffsets(widths, gaps.gapX)
  const rowY = trackOffsets(heights, gaps.gapY)

  const placements = boxes.map((box, index) => {
    const column = index % columnCount
    const row = Math.floor(index / columnCount)
    return {
      id: box.id,
      x: columnX[column]! + (widths[column]! - box.w) / 2,
      y: rowY[row]!,
    }
  })
  const lastColumn = widths.length - 1
  const lastRow = heights.length - 1
  return {
    placements,
    width: columnX[lastColumn]! + widths[lastColumn]!,
    height: rowY[lastRow]! + heights[lastRow]!,
    columnCount,
    rowCount: heights.length,
  }
}
