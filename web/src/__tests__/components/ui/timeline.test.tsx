import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Timeline, type TimelineFrame } from '@/components/ui/timeline'

const FRAMES: readonly TimelineFrame[] = [
  { turnIndex: 1, status: 'in_progress' },
  { turnIndex: 2, status: 'blocked' },
  { turnIndex: 3, status: 'completed' },
]

describe('Timeline', () => {
  it('renders a dot per frame labelled by turn index', () => {
    render(<Timeline frames={FRAMES} currentIndex={0} onSeek={() => {}} />)
    expect(screen.getByRole('button', { name: /Turn 1 \(in_progress\)/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Turn 3 \(completed\)/ })).toBeInTheDocument()
  })

  it('marks the current frame via aria-current', () => {
    render(<Timeline frames={FRAMES} currentIndex={1} onSeek={() => {}} />)
    const current = screen.getByRole('button', { name: /Turn 2/ })
    expect(current).toHaveAttribute('aria-current', 'true')
  })

  it('seeks when a dot is clicked', () => {
    const onSeek = vi.fn()
    render(<Timeline frames={FRAMES} currentIndex={0} onSeek={onSeek} />)
    fireEvent.click(screen.getByRole('button', { name: /Turn 3/ }))
    expect(onSeek).toHaveBeenCalledWith(2)
  })

  it('steps with arrow keys', () => {
    const onSeek = vi.fn()
    render(<Timeline frames={FRAMES} currentIndex={0} onSeek={onSeek} />)
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'ArrowRight' })
    expect(onSeek).toHaveBeenCalledWith(1)
  })

  it('exposes slider value bounds', () => {
    render(<Timeline frames={FRAMES} currentIndex={2} onSeek={() => {}} />)
    const slider = screen.getByRole('slider')
    expect(slider).toHaveAttribute('aria-valuemax', '3')
    expect(slider).toHaveAttribute('aria-valuenow', '3')
  })
})
