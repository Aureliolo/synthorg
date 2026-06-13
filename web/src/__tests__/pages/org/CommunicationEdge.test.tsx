import { render } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { Position } from '@xyflow/react'

// Module-level mock component (eslint-react requires top-level definitions)
function MockBaseEdge({ id, path, style }: { id: string; path: string; style: Record<string, unknown> }) {
  return <path data-testid={`edge-${id}`} d={path} style={style} />
}

vi.mock('@xyflow/react', () => ({
  BaseEdge: MockBaseEdge,
  getBezierPath: () => ['M0 0 C50 0 50 100 100 100'],
  Position: { Top: 'top', Bottom: 'bottom', Left: 'left', Right: 'right' },
}))

const mockPrefersReducedMotion = vi.fn(() => false)

vi.mock('@/lib/motion', () => ({
  prefersReducedMotion: () => mockPrefersReducedMotion(),
}))

// Import after mock
import { CommunicationEdge } from '@/pages/org/CommunicationEdge'

const baseProps = {
  id: 'e-1',
  source: 'a',
  target: 'b',
  sourceX: 0,
  sourceY: 0,
  targetX: 100,
  targetY: 100,
  sourcePosition: Position.Bottom,
  targetPosition: Position.Top,
  sourceHandleId: null,
  targetHandleId: null,
  data: { volume: 10, frequency: 5, maxVolume: 20 },
  selected: false,
  animated: false,
  interactionWidth: 20,
  type: 'communication' as const,
  deletable: false,
  selectable: false,
  focusable: false,
  hidden: false,
  reconnectable: false,
  zIndex: 0,
} as const

describe('CommunicationEdge', () => {
  beforeEach(() => {
    mockPrefersReducedMotion.mockReturnValue(false)
  })

  it('renders a path element', () => {
    const { container } = render(
      <svg>
        <CommunicationEdge {...baseProps} />
      </svg>,
    )
    const path = container.querySelector('[data-testid="edge-e-1"]')
    expect(path).toBeInTheDocument()
  })

  it('applies accent color stroke', () => {
    const { container } = render(
      <svg>
        <CommunicationEdge {...baseProps} />
      </svg>,
    )
    const path = container.querySelector('[data-testid="edge-e-1"]') as HTMLElement
    expect(path.style.stroke).toBe('var(--color-accent)')
  })

  it('scales stroke width with volume', () => {
    const lowVolume = render(
      <svg>
        <CommunicationEdge {...baseProps} id="low" data={{ volume: 1, frequency: 1, maxVolume: 50 }} />
      </svg>,
    )
    const highVolume = render(
      <svg>
        <CommunicationEdge {...baseProps} id="high" data={{ volume: 50, frequency: 10, maxVolume: 50 }} />
      </svg>,
    )

    const lowPath = lowVolume.container.querySelector('[data-testid="edge-low"]') as HTMLElement
    const highPath = highVolume.container.querySelector('[data-testid="edge-high"]') as HTMLElement

    const lowWidth = parseFloat(lowPath.style.strokeWidth)
    const highWidth = parseFloat(highPath.style.strokeWidth)

    expect(highWidth).toBeGreaterThan(lowWidth)
  })

  it('includes dash animation style', () => {
    const { container } = render(
      <svg>
        <CommunicationEdge {...baseProps} />
      </svg>,
    )
    const path = container.querySelector('[data-testid="edge-e-1"]') as HTMLElement
    expect(path.style.strokeDasharray).toBe('var(--so-dash-wide)')
    expect(path.style.animation).toContain('linear infinite')
  })

  it('omits animation when prefers-reduced-motion is active', () => {
    mockPrefersReducedMotion.mockReturnValue(true)
    const { container } = render(
      <svg>
        <CommunicationEdge {...baseProps} />
      </svg>,
    )
    const path = container.querySelector('[data-testid="edge-e-1"]') as HTMLElement
    expect(path.style.animation).toBe('')
  })

  it('injects shared keyframe into document head', () => {
    render(
      <svg>
        <CommunicationEdge {...baseProps} />
      </svg>,
    )
    // Keyframes are injected into document.head, not inline
    const headStyles = Array.from(document.head.querySelectorAll('style'))
    const keyframeStyle = headStyles.find((s) => s.textContent.includes('comm-dash'))
    expect(keyframeStyle).toBeDefined()
    expect(keyframeStyle!.textContent).toContain('@keyframes')
    expect(keyframeStyle!.textContent).toContain('stroke-dashoffset')
  })
})
