import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, it, expect } from 'vitest'
import { apiError, apiSuccess, paginatedFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { listFailoverEvents } from '@/api/endpoints/providers'
import type { FailoverDeclaration } from '@/api/types/providers'
import { ProviderFailoverSection } from '../ProviderFailoverSection'

const DECLARATION = '/api/v1/providers/failover'
const EVENTS = '/api/v1/providers/failover-events'

function declaration(
  overrides: Partial<FailoverDeclaration> = {},
): FailoverDeclaration {
  return {
    enabled: true,
    routes: [
      {
        declared_provider: 'example-provider',
        declared_model: 'example-expert-001',
        alternate_provider: 'test-provider',
        alternate_model: 'example-capable-001',
      },
    ],
    ...overrides,
  }
}

describe('ProviderFailoverSection', () => {
  it('renders each declared route as its two pairs', async () => {
    render(<ProviderFailoverSection />)

    // The same pair appears in the route table and in the engagement beneath
    // it, which is the point: the log says the declaration was what fired.
    expect(
      await screen.findAllByText('example-provider / example-expert-001'),
    ).toHaveLength(2)
    expect(
      screen.getAllByText('test-provider / example-capable-001'),
    ).toHaveLength(2)
  })

  it('reports the mechanism as off when it is', async () => {
    // A route declared while the mechanism is off is inert, so the two
    // halves are reported together rather than one implying the other.
    server.use(
      http.get(DECLARATION, () =>
        HttpResponse.json(apiSuccess(declaration({ enabled: false }))),
      ),
    )
    render(<ProviderFailoverSection />)

    expect(await screen.findByText('Off')).toBeInTheDocument()
  })

  it('says why and at which stage an engagement happened', async () => {
    render(<ProviderFailoverSection />)

    expect(await screen.findByText('engine.reasoning_model')).toBeInTheDocument()
    expect(screen.getByText('overloaded, not tried')).toBeInTheDocument()
  })

  it('explains an undeclared installation instead of an empty table', async () => {
    server.use(
      http.get(DECLARATION, () =>
        HttpResponse.json(apiSuccess(declaration({ routes: [] }))),
      ),
    )
    render(<ProviderFailoverSection />)

    expect(await screen.findByText('No routes declared')).toBeInTheDocument()
  })

  it('distinguishes no engagement from no routes', async () => {
    // Declared but never needed is the healthy state, and it must not read
    // the same as never configured.
    server.use(
      http.get(EVENTS, () =>
        HttpResponse.json(
          paginatedFor<typeof listFailoverEvents>({
            data: [],
            limit: 50,
            nextCursor: null,
            hasMore: false,
            pagination: { limit: 50, next_cursor: null, has_more: false },
          }),
        ),
      ),
    )
    render(<ProviderFailoverSection />)

    expect(await screen.findByText(/No engagement recorded/)).toBeInTheDocument()
    expect(screen.queryByText('No routes declared')).not.toBeInTheDocument()
  })

  it('surfaces a load failure with a retry', async () => {
    server.use(
      http.get(DECLARATION, () => HttpResponse.json(apiError('boom'), { status: 500 })),
    )
    render(<ProviderFailoverSection />)

    expect(await screen.findByText('Could not load failover')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})
