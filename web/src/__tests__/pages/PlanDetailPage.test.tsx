import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { UsePlanDetailDataReturn } from '@/hooks/usePlanDetailData'

import { makePlan, makePlanItem } from '../helpers/factories'

const plan = makePlan('plan-1', {
  objective_title: 'Ship the Tetris game',
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
  it('leads with the objective title and lists items in view mode', () => {
    renderPage()
    // The headline is the objective's human title, denormalised onto the plan,
    // never a raw id.
    expect(
      screen.getByRole('heading', { name: 'Ship the Tetris game' }),
    ).toBeInTheDocument()
    // Titles surface both in the attention worklist and the item card.
    expect(screen.getAllByText(/Scaffold the board/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Piece movement/).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /Rework items/ })).toBeInTheDocument()
  })

  it('surfaces review signals for a high-stakes item', () => {
    hookReturn = {
      ...defaultHookReturn,
      plan: makePlan('plan-1', {
        objective_title: 'Ship the Tetris game',
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

  it('surfaces the failure banner with its reason for a FAILED plan', () => {
    hookReturn = {
      ...defaultHookReturn,
      plan: makePlan('plan-1', {
        status: 'failed',
        failure_reason: 'DecompositionError: model returned no plan',
        items: [],
      }),
    }
    renderPage()
    expect(screen.getByText('Plan processing failed')).toBeInTheDocument()
    expect(
      screen.getByText(/model returned no plan/),
    ).toBeInTheDocument()
    // A failed plan is terminal: no rework affordance.
    expect(
      screen.queryByRole('button', { name: /Rework items/ }),
    ).not.toBeInTheDocument()
  })

  it('falls back to default copy when a FAILED plan has no reason', () => {
    hookReturn = {
      ...defaultHookReturn,
      plan: makePlan('plan-1', {
        status: 'failed',
        failure_reason: null,
        items: [],
      }),
    }
    renderPage()
    expect(screen.getByText('Plan processing failed')).toBeInTheDocument()
    expect(
      screen.getByText(/could not be completed/),
    ).toBeInTheDocument()
  })

  describe('a plan the org is already running', () => {
    function renderExecuting() {
      hookReturn = {
        ...defaultHookReturn,
        plan: makePlan('plan-1', {
          status: 'executing',
          objective_title: 'Ship the Tetris game',
          items: [makePlanItem('i1', { title: 'Scaffold the board' })],
        }),
      }
      return renderPage()
    }

    it('offers the run to watch rather than no control at all', () => {
      // The page carried nothing an operator could act on at this status: the
      // review controls are gone by design and the only other affordance was
      // gated on `approved`, the status a plan leaves the moment work starts.
      renderExecuting()

      expect(screen.getByRole('link', { name: /Watch it run/ })).toBeInTheDocument()
    })

    it('stops asking for a review it has already been given', () => {
      renderExecuting()

      expect(screen.queryByText('Needs your review')).toBeNull()
      expect(screen.queryByText('Needs your attention')).toBeNull()
      expect(screen.queryByRole('button', { name: /Request changes/ })).toBeNull()
    })
  })
})
