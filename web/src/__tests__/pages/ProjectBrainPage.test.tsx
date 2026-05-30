import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router'
import { describe, expect, it } from 'vitest'
import type {
  getProjectBrainEntry,
  listProjectBrain,
} from '@/api/endpoints/projectBrain'
import type { BrainEntry, BrainSummary } from '@/api/types'
import {
  apiError,
  apiPaginatedError,
  emptyPage,
  paginatedFor,
  successFor,
} from '@/mocks/handlers'
import ProjectBrainPage from '@/pages/ProjectBrainPage'
import { server } from '@/test-setup'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:projectId/brain" element={<ProjectBrainPage />} />
        <Route
          path="/projects/:projectId/brain/:entryId"
          element={<ProjectBrainPage />}
        />
      </Routes>
    </MemoryRouter>,
  )
}

function summary(overrides: Partial<BrainSummary> = {}): BrainSummary {
  return {
    project_id: 'p1',
    entry_id: 'e1',
    revision: 1,
    entry_kind: 'decision',
    title: 'Adopt event sourcing',
    status: 'accepted',
    author: 'agent_alice',
    recorded_at: '2026-05-30T00:00:00Z',
    tags: [],
    ...overrides,
  }
}

function entry(overrides: Partial<BrainEntry> = {}): BrainEntry {
  return {
    entry_id: 'e1',
    revision: 1,
    project_id: 'p1',
    entry_kind: 'decision',
    title: 'Adopt event sourcing',
    rationale: 'We need a full audit trail.',
    status: 'accepted',
    author: 'agent_alice',
    recorded_at: '2026-05-30T00:00:00Z',
    related_task_ids: [],
    related_entry_ids: [],
    supersedes_entry_id: null,
    tags: [],
    confidence: null,
    citations: [],
    payload: { entry_kind: 'decision', decision_outcome: 'event-sourcing', alternatives: [] },
    ...overrides,
  }
}

describe('ProjectBrainPage', () => {
  it('renders the breadcrumb', () => {
    renderAt('/projects/p1/brain')
    expect(screen.getByText('Brain')).toBeInTheDocument()
  })

  it('shows the empty state from the default handler', async () => {
    renderAt('/projects/p1/brain')
    expect(
      await screen.findByText('No matching brain entries.'),
    ).toBeInTheDocument()
  })

  it('groups current-state entries by kind', async () => {
    server.use(
      http.get('/api/v1/projects/:projectId/brain', () =>
        HttpResponse.json(
          paginatedFor<typeof listProjectBrain>({
            ...emptyPage<BrainSummary>(),
            data: [
              summary(),
              summary({
                entry_id: 'e2',
                entry_kind: 'blocker',
                title: 'Staging is down',
                status: 'blocked',
              }),
            ],
          }),
        ),
      ),
    )
    renderAt('/projects/p1/brain')
    expect(await screen.findByText('Adopt event sourcing')).toBeInTheDocument()
    expect(screen.getByText('Staging is down')).toBeInTheDocument()
    // "Decisions" / "Blockers" appear as both filter chips and section
    // headings; assert the section headings specifically.
    expect(screen.getByRole('heading', { name: 'Decisions' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Blockers' })).toBeInTheDocument()
  })

  it('renders the error banner when the list fetch fails', async () => {
    server.use(
      http.get('/api/v1/projects/:projectId/brain', () =>
        HttpResponse.json(apiPaginatedError('brain boom'), { status: 500 }),
      ),
    )
    renderAt('/projects/p1/brain')
    expect(await screen.findByText('Could not load brain')).toBeInTheDocument()
  })

  it('shows the selected entry detail with its rationale and payload', async () => {
    server.use(
      http.get('/api/v1/projects/:projectId/brain/:entryId', () =>
        HttpResponse.json(successFor<typeof getProjectBrainEntry>(entry())),
      ),
    )
    renderAt('/projects/p1/brain/e1')
    expect(await screen.findByText('We need a full audit trail.')).toBeInTheDocument()
    expect(screen.getByText('event-sourcing')).toBeInTheDocument()
    expect(screen.getByText('Show revision history')).toBeInTheDocument()
  })

  it('surfaces a per-entry error', async () => {
    server.use(
      http.get('/api/v1/projects/:projectId/brain/:entryId', () =>
        HttpResponse.json(apiError('nope'), { status: 500 }),
      ),
    )
    renderAt('/projects/p1/brain/e1')
    expect(await screen.findByText('Could not load this entry.')).toBeInTheDocument()
  })
})
