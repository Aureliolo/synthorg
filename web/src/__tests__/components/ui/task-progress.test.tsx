import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { TaskProgress } from '@/components/ui/task-progress'
import type { ProgressStage } from '@/components/ui/progress-indicator'

const STAGES: ProgressStage[] = [
  { id: 's1', label: 'Step 1', status: 'done' },
  { id: 's2', label: 'Step 2', status: 'running', description: 'search, read_file' },
]

describe('TaskProgress', () => {
  it('shows an indeterminate starting state before any stage arrives', () => {
    render(<TaskProgress status="running" stages={[]} />)
    const region = screen.getByText('Working').closest('[aria-live]')
    expect(region).toHaveAttribute('aria-busy', 'true')
  })

  it('renders accumulated stages while running', () => {
    render(<TaskProgress status="running" stages={STAGES} />)
    expect(screen.getByText('Working')).toBeInTheDocument()
    expect(screen.getByText('Step 1')).toBeInTheDocument()
    expect(screen.getByText('Step 2')).toBeInTheDocument()
  })

  it('surfaces a finished header', () => {
    render(<TaskProgress status="finished" stages={STAGES} />)
    const region = screen.getByText('Run finished').closest('[aria-live]')
    expect(region).toHaveAttribute('aria-busy', 'false')
  })

  it('surfaces a failed header', () => {
    render(<TaskProgress status="error" stages={STAGES} />)
    expect(screen.getByText('Run failed')).toBeInTheDocument()
  })

  it('surfaces a disconnected header without a busy spinner', () => {
    render(<TaskProgress status="disconnected" stages={STAGES} />)
    const region = screen.getByText('Live updates unavailable').closest('[aria-live]')
    expect(region).toHaveAttribute('aria-busy', 'false')
  })
})
