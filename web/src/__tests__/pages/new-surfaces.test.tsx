/**
 * Smoke tests for the 9 new dashboard surfaces scaffolded for issue #1687.
 *
 * Each test asserts the page renders without crashing under the default
 * MSW handlers (workflow-executions / webhooks handlers were added in
 * the same PR; version-history pages reuse the existing
 * VersionHistorySection mocks; analytics / meta / setup endpoints were
 * already mocked).
 *
 * The point is not full UX coverage; it is to fail fast if a page's
 * imports break, the route binds the wrong endpoint, or a Pydantic
 * field rename lands on one side of the wire without the other.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import BudgetVersionsPage from '@/pages/BudgetVersionsPage'
import CompanyVersionsPage from '@/pages/CompanyVersionsPage'
import EvaluationVersionsPage from '@/pages/EvaluationVersionsPage'
import WorkflowVersionsPage from '@/pages/WorkflowVersionsPage'
import WorkflowExecutionsPage from '@/pages/WorkflowExecutionsPage'
import WebhookReceiptsPage from '@/pages/WebhookReceiptsPage'
import CoordinationMetricsPage from '@/pages/CoordinationMetricsPage'
import MetaAnalyticsPage from '@/pages/MetaAnalyticsPage'
import PersonalitiesAdminPage from '@/pages/PersonalitiesAdminPage'

function renderAt(node: React.ReactNode, path: string, route: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={route} element={node} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('new-surface pages render', () => {
  it('BudgetVersionsPage renders the title', async () => {
    renderAt(<BudgetVersionsPage />, '/budget/versions', '/budget/versions')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Budget configuration history/i })).toBeInTheDocument()
    })
  })

  it('CompanyVersionsPage renders the title', async () => {
    renderAt(<CompanyVersionsPage />, '/org/versions', '/org/versions')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Company structure history/i })).toBeInTheDocument()
    })
  })

  it('EvaluationVersionsPage renders the title', async () => {
    renderAt(<EvaluationVersionsPage />, '/evaluation/versions', '/evaluation/versions')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Evaluation configuration history/i })).toBeInTheDocument()
    })
  })

  it('WorkflowVersionsPage renders the title with a workflow id', async () => {
    renderAt(<WorkflowVersionsPage />, '/workflows/wf-1/versions', '/workflows/:id/versions')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Workflow versions/i })).toBeInTheDocument()
    })
  })

  it('WorkflowExecutionsPage renders the title with a workflow id', async () => {
    renderAt(<WorkflowExecutionsPage />, '/workflows/wf-1/executions', '/workflows/:id/executions')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Workflow executions/i })).toBeInTheDocument()
    })
  })

  it('WebhookReceiptsPage renders the title', async () => {
    renderAt(<WebhookReceiptsPage />, '/integrations/webhooks/receipts', '/integrations/webhooks/receipts')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Webhook receipts/i })).toBeInTheDocument()
    })
  })

  it('CoordinationMetricsPage renders the title', async () => {
    renderAt(<CoordinationMetricsPage />, '/analytics/coordination', '/analytics/coordination')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Coordination metrics/i })).toBeInTheDocument()
    })
  })

  it('MetaAnalyticsPage renders the title', async () => {
    renderAt(<MetaAnalyticsPage />, '/analytics/meta', '/analytics/meta')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Meta analytics/i })).toBeInTheDocument()
    })
  })

  it('PersonalitiesAdminPage renders the title', async () => {
    renderAt(<PersonalitiesAdminPage />, '/admin/personalities', '/admin/personalities')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Personality presets/i })).toBeInTheDocument()
    })
  })
})
