import { fireEvent, render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'

import type { getCockpitSnapshot, getRedTeamReport } from '@/api/endpoints/cockpit'
import MissionControlPage from '@/pages/MissionControlPage'
import { successFor } from '@/mocks/handlers'
import { useMissionControlStore } from '@/stores/mission-control'
import { useSteeringStore } from '@/stores/steering'
import { server } from '@/test-setup'

function renderPage(initialEntries: string[] = ['/']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <MissionControlPage />
    </MemoryRouter>,
  )
}

afterEach(() => {
  useMissionControlStore.setState({
    snapshot: null,
    snapshotLoading: false,
    snapshotError: null,
    frames: [],
    framesExecutionId: null,
    framesLoading: false,
    framesError: null,
    seekView: null,
    redTeamReport: null,
  })
  useSteeringStore.setState({
    directives: [],
    directivesProject: null,
    directivesLoading: false,
    directivesError: null,
    pendingProposal: null,
  })
})

describe('MissionControlPage', () => {
  it('renders the cockpit heading and live KPIs', async () => {
    renderPage()
    expect(
      screen.getByRole('heading', { name: 'Mission Control' }),
    ).toBeInTheDocument()
    expect(await screen.findByText('Active agents')).toBeInTheDocument()
    expect(screen.getByText('Stuck')).toBeInTheDocument()
    expect(screen.getByText('Runaway')).toBeInTheDocument()
  })

  it('surfaces a stuck agent with an intervention control', async () => {
    server.use(
      http.get('/api/v1/cockpit/snapshot', () =>
        HttpResponse.json(
          successFor<typeof getCockpitSnapshot>({
            timestamp: '2026-05-22T12:00:00Z',
            agents: [
              {
                agent_id: 'agent-1',
                task_id: 'task-1',
                execution_id: 'exec-1',
                status: 'in_progress',
                turn_count: 4,
                cost: 1.25,
                last_active: '2026-05-22T11:30:00Z',
                is_stuck: true,
                is_runaway: false,
              },
            ],
            total_cost: 1.25,
            active_count: 1,
            stuck_agents: ['agent-1'],
            runaway_agents: [],
          }),
        ),
      ),
    )

    renderPage()
    expect(await screen.findByText('agent-1')).toBeInTheDocument()
    expect(screen.getByText('stuck')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Kill' })).toBeInTheDocument()
  })

  it('switches to the flight recorder tab', async () => {
    renderPage()
    await screen.findByText('Active agents')
    fireEvent.click(screen.getByRole('radio', { name: 'Flight Recorder' }))
    expect(screen.getByText('No frames loaded')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Load run' })).toBeInTheDocument()
  })

  it('deep-links from a live agent row into the flight recorder and replays', async () => {
    renderPage()
    const replay = await screen.findByRole('button', { name: 'Replay' })
    fireEvent.click(replay)
    // The recorder tab is now active and auto-loaded the run; the
    // playback control proves frames loaded for exec-1.
    expect(await screen.findByRole('button', { name: 'Play' })).toBeInTheDocument()
  })

  it('surfaces the durable red-team verdict for a replayed run', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Replay' }))
    expect(await screen.findByText('Red-team review')).toBeInTheDocument()
    expect(screen.getByText('Blocked')).toBeInTheDocument()
    expect(
      screen.getByText('Hardcoded credential in the deliverable.'),
    ).toBeInTheDocument()
  })

  it('shows no red-team panel when the run has no recorded verdict', async () => {
    server.use(
      http.get('/api/v1/cockpit/flight-recorder/:executionId/red-team', () =>
        HttpResponse.json(successFor<typeof getRedTeamReport>(null)),
      ),
    )
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Replay' }))
    await screen.findByRole('button', { name: 'Play' })
    expect(screen.queryByText('Red-team review')).not.toBeInTheDocument()
  })

  it('switches to the steering tab and seeds the project from the URL', async () => {
    renderPage(['/?project=checkout'])
    await screen.findByText('Active agents')
    fireEvent.click(screen.getByRole('radio', { name: 'Steering' }))
    expect(screen.getByLabelText('Project')).toHaveValue('checkout')
    expect(await screen.findByText('use Postgres not Mongo')).toBeInTheDocument()
  })
})
