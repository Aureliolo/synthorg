import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { UsePlanDetailDataReturn } from '@/hooks/usePlanDetailData'

import { makePlan, makePlanItem } from '../helpers/factories'

const plan = makePlan('plan-1', {
  objective_id: 'ship-the-game',
  items: [
    makePlanItem('i1', { title: 'Scaffold the board' }),
    makePlanItem('i2', { title: 'Piece movement' }),
  ],
})

const defaultHookReturn: UsePlanDetailDataReturn = {
  plan,
  parentTaskTitle: 'Ship the Tetris game',
  loading: false,
  error: null,
  wsConnected: true,
  wsSetupError: null,
}

let hookReturn = { ...defaultHookReturn }

const getDetailData = vi.fn(() => hookReturn)
vi.mock('@/hooks/usePlanDetailData', () => {
  const hookName = 'usePlanDetailData'
  return { [hookName]: () => getDetailData() }
})

import PlanDetailPage from '@/pages/PlanDetailPage'

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/plans/plan-1']}>
      <Routes>
        <Route path="/plans/:planId" element={<PlanDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  hookReturn = { ...defaultHookReturn }
})

describe('PlanDetailPage', () => {
  it('leads with the human headline and lists items in view mode', () => {
    renderPage()
    // Headline resolves from the parent objective task, not the raw id.
    expect(
      screen.getByRole('heading', { name: 'Ship the Tetris game' }),
    ).toBeInTheDocument()
    // The objective id is demoted to a labelled reference field.
    expect(screen.getByText('ship-the-game')).toBeInTheDocument()
    // Titles surface both in the attention worklist and the item card.
    expect(screen.getAllByText(/Scaffold the board/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Piece movement/).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /Rework items/ })).toBeInTheDocument()
  })

  it('falls back to the objective id when no parent title resolves', () => {
    hookReturn = { ...defaultHookReturn, parentTaskTitle: null }
    renderPage()
    expect(
      screen.getByRole('heading', { name: 'ship-the-game' }),
    ).toBeInTheDocument()
  })

  it('surfaces review signals for a high-stakes item', () => {
    hookReturn = {
      ...defaultHookReturn,
      plan: makePlan('plan-1', {
        objective_id: 'ship-the-game',
        items: [
          makePlanItem('i1', {
            title: 'Design the netcode',
            stakes: 'critical',
            acceptance_criteria: [],
          }),
        ],
      }),
    }
    renderPage()
    expect(screen.getByText('Needs your review')).toBeInTheDocument()
    expect(screen.getByText('Needs your attention')).toBeInTheDocument()
    expect(screen.getAllByText('Critical stakes').length).toBeGreaterThan(0)
  })

  it('shows a not-found banner on error with no plan', () => {
    hookReturn = {
      plan: null,
      parentTaskTitle: null,
      loading: false,
      error: 'gone',
      wsConnected: true,
      wsSetupError: null,
    }
    renderPage()
    expect(screen.getByText('Plan not found')).toBeInTheDocument()
  })

  it('hides rework actions once the plan is approved', () => {
    hookReturn = {
      ...defaultHookReturn,
      plan: makePlan('plan-1', { status: 'approved' }),
    }
    renderPage()
    expect(
      screen.queryByRole('button', { name: /Rework items/ }),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Watch it run/ })).toBeInTheDocument()
  })
})
