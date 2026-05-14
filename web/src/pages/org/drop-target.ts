/**
 * Drop target detection for drag-drop agent reassignment.
 *
 * Determines which department group node a dragged agent overlaps with
 * based on point-in-rectangle hit testing.
 */

export interface DepartmentBounds {
  departmentName: string
  nodeId: string
  x: number
  y: number
  width: number
  height: number
}

/**
 * Find the department group node that contains the given point.
 *
 * When multiple departments overlap, returns the one with the smallest area
 * (most specific hit). Returns null if the point is outside all departments.
 */
export function findDropTarget(
  point: { x: number; y: number },
  departments: DepartmentBounds[],
): DepartmentBounds | null {
  let best: DepartmentBounds | null = null
  let bestArea = Infinity

  for (const dept of departments) {
    if (
      point.x >= dept.x &&
      point.x <= dept.x + dept.width &&
      point.y >= dept.y &&
      point.y <= dept.y + dept.height
    ) {
      const area = dept.width * dept.height
      if (area < bestArea) {
        best = dept
        bestArea = area
      }
    }
  }

  return best
}
