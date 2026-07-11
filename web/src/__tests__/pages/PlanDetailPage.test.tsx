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
  it('renders the plan header and items in view mode', () => {
    renderPage()
    expect(screen.getByText('ship-the-game')).toBeInTheDocument()
    expect(screen.getByText(/Scaffold the board/)).toBeInTheDocument()
    expect(screen.getByText(/Piece movement/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Rework items/ })).toBeInTheDocument()
  })

  it('shows a not-found banner on error with no plan', () => {
    hookReturn = {
      plan: null,
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
      plan: makePlan('plan-1', { status: 'approved' }),
      loading: false,
      error: null,
      wsConnected: true,
      wsSetupError: null,
    }
    renderPage()
    expect(
      screen.queryByRole('button', { name: /Rework items/ }),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Watch it run/ })).toBeInTheDocument()
  })
})
