import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router'

import type { TrainingPlanResponse } from '@/api/endpoints/training'
import {
  TrainingPlanTable,
  type TrainingPlanRow,
} from '@/pages/training/TrainingPlanTable'

// id != name so a regression that addresses an agent by name instead
// of id is caught: the link href and the onExecute argument would carry
// the name instead of this UUID.
const AGENT_ID = '11111111-2222-3333-4444-555555555555'
const AGENT_NAME = 'Alice'

function makePlan(): TrainingPlanResponse {
  return {
    id: 'plan-1',
    new_agent_id: AGENT_ID,
    new_agent_role: 'engineer',
    source_selector_type: 'all',
    enabled_content_types: ['procedural'],
    curation_strategy_type: 'merge',
    volume_caps: [],
    override_sources: [],
    skip_training: false,
    require_review: false,
    status: 'pending',
    created_at: '2026-04-19T00:00:00Z',
    executed_at: null,
  }
}

function renderTable(onExecute: (agentId: string) => void = vi.fn()) {
  const rows: TrainingPlanRow[] = [
    { agentId: AGENT_ID, agentName: AGENT_NAME, plan: makePlan(), result: null },
  ]
  render(
    <MemoryRouter>
      <TrainingPlanTable rows={rows} onExecute={onExecute} />
    </MemoryRouter>,
  )
}

describe('TrainingPlanTable', () => {
  it('links to the detail page by stable id while displaying the name', () => {
    renderTable()
    const link = screen.getByRole('link', { name: AGENT_NAME })
    expect(link).toHaveAttribute('href', `/agents/${AGENT_ID}`)
  })

  it('executes a pending plan by agent id, not name', () => {
    const onExecute = vi.fn()
    renderTable(onExecute)
    fireEvent.click(screen.getByRole('button', { name: /execute/i }))
    expect(onExecute).toHaveBeenCalledWith(AGENT_ID)
  })
})
