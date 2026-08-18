import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Position } from '@xyflow/react'

/**
 * The stroke, which is the half the geometry tests cannot see.
 *
 * Siblings share their trunk, bus and riser exactly, so those spans are drawn
 * once per edge on top of each other. At 0.7 alpha the overlaps composited to
 * 0.91 and 0.97 and the shared trunk read as a line with brightness steps in
 * it. Opacity is therefore load-bearing by its absence, and nothing else in the
 * suite would notice a restyle putting it back.
 */

function MockBaseEdge({
  id,
  path,
  style,
}: {
  id: string
  path: string
  style: Record<string, unknown>
}) {
  return <path data-testid={`edge-${id}`} d={path} style={style} />
}

vi.mock('@xyflow/react', () => ({
  BaseEdge: MockBaseEdge,
  Position: { Top: 'top', Bottom: 'bottom', Left: 'left', Right: 'right' },
}))

vi.mock('motion/react', () => ({
  useReducedMotion: () => false,
}))

// Imported after the mock so the edge renders against it.
import { HierarchyEdge } from '@/pages/org/HierarchyEdge'

const baseProps = {
  id: 'h-1',
  source: 'root',
  target: 'dept',
  sourceX: 0,
  sourceY: 100,
  targetX: 200,
  targetY: 300,
  sourcePosition: Position.Bottom,
  targetPosition: Position.Top,
  sourceHandleId: null,
  targetHandleId: null,
  data: { trunkY: 200, busY: 200 },
  selected: false,
  animated: false,
  interactionWidth: 20,
  type: 'hierarchy' as const,
  deletable: false,
  selectable: false,
  focusable: false,
  hidden: false,
  reconnectable: false,
  zIndex: 0,
} as const

describe('HierarchyEdge', () => {
  it('draws an opaque stroke, so coinciding siblings read as one trunk', () => {
    const { getByTestId } = render(<HierarchyEdge {...baseProps} />)
    const style = getByTestId('edge-h-1').style
    expect(style.stroke).toBe('var(--color-border-bright)')
    expect(style.opacity).toBe('')
    expect(style.strokeOpacity).toBe('')
  })

  it('draws the routed corners it was handed', () => {
    const { getByTestId } = render(<HierarchyEdge {...baseProps} />)
    expect(getByTestId('edge-h-1').getAttribute('d')).toBe(
      'M0,100 L0,200 L200,200 L200,300',
    )
  })
})
