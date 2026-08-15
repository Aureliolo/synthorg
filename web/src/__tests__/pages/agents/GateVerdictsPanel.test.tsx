import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { GateVerdictsPanel } from '@/pages/agents/GateVerdictsPanel'
import { gateForRole } from '@/pages/agents/useGateVerdicts'
import { server } from '@/test-setup'

describe('gateForRole', () => {
  it('maps each gate role onto its archive, and nothing else', () => {
    expect(gateForRole('Completion Reviewer')).toBe('completion_oracle')
    expect(gateForRole('  red team ')).toBe('red_team')
    expect(gateForRole('Developer')).toBeNull()
  })
})

describe('GateVerdictsPanel', () => {
  it('reports the reviewer verdict counts the backend counted', async () => {
    render(<GateVerdictsPanel agentId="agent-1" gate="completion_oracle" />)

    await screen.findByText('Peer-review verdicts')
    // The tally is the backend's own total, not a count of the rows shown:
    // one row is on screen and the total reads four.
    expect(await screen.findByText('4')).toBeInTheDocument()
    // Twice: once as the tally's label, once as the row's verdict badge.
    expect(await screen.findAllByText('Approved')).toHaveLength(2)
    expect(screen.getByText('Rejected')).toBeInTheDocument()
  })

  it('names the model a verdict actually ran on', async () => {
    render(<GateVerdictsPanel agentId="agent-1" gate="completion_oracle" />)

    expect(
      await screen.findByText(/example-capable-001 \(capable\)/),
    ).toBeInTheDocument()
  })

  it('reports the adversary verdicts for a red-team holder', async () => {
    render(<GateVerdictsPanel agentId="agent-1" gate="red_team" />)

    await screen.findByText('Adversarial verdicts')
    expect(await screen.findAllByText('Passed')).toHaveLength(2)
    expect(screen.getByText(/example-expert-001 \(expert\)/)).toBeInTheDocument()
  })

  it('says nothing has been judged rather than showing an empty table', async () => {
    server.use(
      http.get('/api/v1/completion-oracle/reports/summary', () =>
        HttpResponse.json({
          success: true,
          data: { total: 0, by_verdict: {} },
        }),
      ),
      http.get('/api/v1/completion-oracle/reports', () =>
        HttpResponse.json({
          success: true,
          data: [],
          pagination: { limit: 20, next_cursor: null, has_more: false },
        }),
      ),
    )
    render(<GateVerdictsPanel agentId="agent-1" gate="completion_oracle" />)

    expect(await screen.findByText('No reviews yet')).toBeInTheDocument()
  })

  it('distinguishes a failed load from an empty record', async () => {
    server.use(
      http.get('/api/v1/completion-oracle/reports/summary', () =>
        HttpResponse.json({ success: false }, { status: 500 }),
      ),
    )
    render(<GateVerdictsPanel agentId="agent-1" gate="completion_oracle" />)

    expect(await screen.findByText(/Failed to load/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('recovers on Retry without unmounting the card under the operator', async () => {
    // The only interactive branch in the controller, and the one that
    // regressed: refetch sets loading again, so a panel that returned null
    // whenever loading was true took the Retry button away mid-click.
    let attempts = 0
    // The retry response is held so the assertion below lands DURING the
    // loading window. Awaiting the click alone would flush past it, and a
    // panel that unmounted while loading and remounted on success would
    // satisfy the assertion without ever holding the button still.
    let releaseRetry = (): void => {}
    const retryHeld = new Promise<void>((resolve) => {
      releaseRetry = resolve
    })
    server.use(
      http.get('/api/v1/completion-oracle/reports/summary', async () => {
        attempts += 1
        if (attempts === 1) {
          return HttpResponse.json({ success: false }, { status: 500 })
        }
        await retryHeld
        return HttpResponse.json({
          success: true,
          data: { total: 4, by_verdict: { approve: 3, reject: 1 } },
        })
      }),
    )
    render(<GateVerdictsPanel agentId="agent-1" gate="completion_oracle" />)
    await screen.findByText(/Failed to load/)

    await userEvent.click(screen.getByRole('button', { name: /retry/i }))

    // Mid-retry, with the response still held: the card holding the button
    // the operator just pressed is still on screen.
    expect(screen.getByText('Peer-review verdicts')).toBeInTheDocument()

    releaseRetry()
    expect(await screen.findByText('4')).toBeInTheDocument()
    expect(screen.queryByText(/Failed to load/)).not.toBeInTheDocument()
  })

  it('drops the previous agent verdicts the moment the subject changes', async () => {
    // The panel is rendered without a key, so a subject change is a prop
    // change on a live component: the request for the new agent is still in
    // flight while the previous agent's totals sit in state.
    let calls = 0
    let releaseSecond = (): void => {}
    const secondHeld = new Promise<void>((resolve) => {
      releaseSecond = resolve
    })
    server.use(
      http.get('/api/v1/completion-oracle/reports/summary', async ({ request }) => {
        calls += 1
        if (calls !== 1) {
          await secondHeld
        }
        const reviewer = new URL(request.url).searchParams.get('reviewer_agent_id')
        return HttpResponse.json({
          success: true,
          data:
            reviewer === 'agent-1'
              ? { total: 4, by_verdict: { approve: 3, reject: 1 } }
              : { total: 12, by_verdict: { approve: 9, reject: 3 } },
        })
      }),
    )
    const view = render(
      <GateVerdictsPanel agentId="agent-1" gate="completion_oracle" />,
    )
    expect(await screen.findByText('4')).toBeInTheDocument()

    view.rerender(<GateVerdictsPanel agentId="agent-2" gate="completion_oracle" />)

    expect(screen.queryByText('4')).not.toBeInTheDocument()

    releaseSecond()
    expect(await screen.findByText('12')).toBeInTheDocument()
  })
})
