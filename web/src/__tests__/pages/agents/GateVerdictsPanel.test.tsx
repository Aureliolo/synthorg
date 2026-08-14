import { render, screen } from '@testing-library/react'
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
})
