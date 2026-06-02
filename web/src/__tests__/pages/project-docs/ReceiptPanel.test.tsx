import { fireEvent, render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import {
  apiError,
  apiSuccess,
  buildDeliverableReceipt,
  buildReceiptValidationResult,
} from '@/mocks/handlers'
import { ReceiptPanel } from '@/pages/project-docs/ReceiptPanel'
import { server } from '@/test-setup'

const RECEIPT_URL = '/api/v1/projects/:projectId/docs/:slug/receipt'
const VALIDATE_URL = '/api/v1/projects/:projectId/docs/:slug/receipt/validate'

function renderPanel() {
  return render(<ReceiptPanel projectId="proj-1" slug="deliverable-x" />)
}

function expandPanel() {
  fireEvent.click(screen.getByRole('button', { name: /provenance receipt/i }))
}

describe('ReceiptPanel', () => {
  it('renders the collapsible heading', () => {
    renderPanel()
    expect(
      screen.getByRole('button', { name: /provenance receipt/i }),
    ).toBeInTheDocument()
  })

  it('renders the six signal groups for a populated receipt', async () => {
    server.use(
      http.get(RECEIPT_URL, ({ params }) =>
        HttpResponse.json(
          apiSuccess(
            buildDeliverableReceipt({
              deliverable_doc_slug: String(params.slug),
              total_cost: 1.25,
              sources: [
                {
                  source_id: 's1',
                  chunk_id: 'c1',
                  title: 'Design doc',
                  uri: 'repo://design.md',
                  content_hash: 'a'.repeat(64),
                },
              ],
              tests: [
                {
                  record_id: 'r1',
                  command: 'pytest -q',
                  returncode: 0,
                  passed: true,
                  timed_out: false,
                  stdout_tail: 'ok',
                  executed_at: '2026-05-20T00:00:00Z',
                },
              ],
            }),
          ),
        ),
      ),
    )
    renderPanel()
    expandPanel()

    expect(await screen.findByText('Design doc')).toBeInTheDocument()
    expect(screen.getByText('repo://design.md')).toBeInTheDocument()
    expect(screen.getByText('pytest -q')).toBeInTheDocument()
    expect(screen.getByText('passed')).toBeInTheDocument()
    expect(screen.getByText('Cost')).toBeInTheDocument()
  })

  it('shows a muted note when no receipt exists', async () => {
    server.use(
      http.get(RECEIPT_URL, () =>
        HttpResponse.json(apiError('no receipt for deliverable'), {
          status: 404,
        }),
      ),
    )
    renderPanel()
    expandPanel()

    expect(
      await screen.findByText(/no receipt for deliverable/i),
    ).toBeInTheDocument()
  })

  it('reports a consistent receipt after Validate', async () => {
    renderPanel()
    expandPanel()
    await screen.findByText('Cost')

    fireEvent.click(screen.getByRole('button', { name: /^validate$/i }))

    expect(
      await screen.findByText(/all present signals are consistent/i),
    ).toBeInTheDocument()
  })

  it('reports inconsistencies when validation fails', async () => {
    server.use(
      http.get(VALIDATE_URL, () =>
        HttpResponse.json(
          apiSuccess(
            buildReceiptValidationResult({
              valid: false,
              errors: ['source s1 does not resolve'],
            }),
          ),
        ),
      ),
    )
    renderPanel()
    expandPanel()
    await screen.findByText('Cost')

    fireEvent.click(screen.getByRole('button', { name: /^validate$/i }))

    expect(
      await screen.findByText(/inconsistency\(ies\) found/i),
    ).toBeInTheDocument()
  })
})
