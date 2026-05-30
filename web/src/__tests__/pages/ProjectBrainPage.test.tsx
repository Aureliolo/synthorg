import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router'
import { describe, expect, it } from 'vitest'
import type {
  getProjectBrainEntry,
  getProjectBrainHistory,
  listProjectBrain,
} from '@/api/endpoints/projectBrain'
import type { BrainEntry, BrainEntryVersion, BrainSummary } from '@/api/types'
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

function version(overrides: Partial<BrainEntryVersion> = {}): BrainEntryVersion {
  return {
    revision: 1,
    commit_hash: '0123456789abcdef',
    committed_at: '2026-05-30T00:00:00Z',
    summary: 'brain(decision): e1 r1',
    author: 'agent_alice',
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

  it('shows a loading skeleton until the list resolves', async () => {
    let releasePage = (): void => {}
    const pageGate = new Promise<void>((resolve) => {
      releasePage = resolve
    })
    server.use(
      http.get('/api/v1/projects/:projectId/brain', async () => {
        await pageGate
        return HttpResponse.json(
          paginatedFor<typeof listProjectBrain>(emptyPage<BrainSummary>()),
        )
      }),
    )
    renderAt('/projects/p1/brain')
    await waitFor(() => {
      expect(
        document.querySelectorAll('[data-skeleton-line]').length,
      ).toBeGreaterThan(0)
    })
    // Release the gated response so the list settles and no fetch is left
    // pending at teardown.
    releasePage()
    expect(
      await screen.findByText('No matching brain entries.'),
    ).toBeInTheDocument()
  })

  it('appends a second page when Load more is clicked', async () => {
    server.use(
      http.get('/api/v1/projects/:projectId/brain', ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor')
        if (cursor === null) {
          return HttpResponse.json(
            paginatedFor<typeof listProjectBrain>({
              ...emptyPage<BrainSummary>(),
              data: [summary({ entry_id: 'e1', title: 'First decision' })],
              nextCursor: 'cursor-2',
              hasMore: true,
            }),
          )
        }
        return HttpResponse.json(
          paginatedFor<typeof listProjectBrain>({
            ...emptyPage<BrainSummary>(),
            data: [summary({ entry_id: 'e2', title: 'Second decision' })],
          }),
        )
      }),
    )
    const user = userEvent.setup()
    renderAt('/projects/p1/brain')
    expect(await screen.findByText('First decision')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Load more' }))
    expect(await screen.findByText('Second decision')).toBeInTheDocument()
    // Page one stays visible; the second page is appended, not replaced.
    expect(screen.getByText('First decision')).toBeInTheDocument()
  })

  it('filters the list to one kind when a kind chip is clicked', async () => {
    server.use(
      http.get('/api/v1/projects/:projectId/brain', () =>
        HttpResponse.json(
          paginatedFor<typeof listProjectBrain>({
            ...emptyPage<BrainSummary>(),
            data: [
              summary({ entry_id: 'e1', title: 'Adopt event sourcing' }),
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
    const user = userEvent.setup()
    renderAt('/projects/p1/brain')
    expect(await screen.findByText('Adopt event sourcing')).toBeInTheDocument()
    expect(screen.getByText('Staging is down')).toBeInTheDocument()
    // The "Blockers" chip is a button; the section heading shares the text.
    await user.click(screen.getByRole('button', { name: 'Blockers' }))
    expect(screen.queryByText('Adopt event sourcing')).not.toBeInTheDocument()
    expect(screen.getByText('Staging is down')).toBeInTheDocument()
  })

  it('distinguishes a history fetch error from empty history', async () => {
    server.use(
      http.get('/api/v1/projects/:projectId/brain/:entryId', () =>
        HttpResponse.json(successFor<typeof getProjectBrainEntry>(entry())),
      ),
      http.get('/api/v1/projects/:projectId/brain/:entryId/history', () =>
        HttpResponse.json(apiError('history boom'), { status: 500 }),
      ),
    )
    const user = userEvent.setup()
    renderAt('/projects/p1/brain/e1')
    await user.click(await screen.findByText('Show revision history'))
    expect(
      await screen.findByText('Could not load revision history.'),
    ).toBeInTheDocument()
    expect(
      screen.queryByText('No committed snapshots for this entry yet.'),
    ).not.toBeInTheDocument()
  })

  it('renders commit summary and author in the revision history', async () => {
    server.use(
      http.get('/api/v1/projects/:projectId/brain/:entryId', () =>
        HttpResponse.json(successFor<typeof getProjectBrainEntry>(entry())),
      ),
      http.get('/api/v1/projects/:projectId/brain/:entryId/history', () =>
        HttpResponse.json(
          successFor<typeof getProjectBrainHistory>([
            version({
              summary: 'brain(decision): adopt event sourcing',
              author: 'agent_historian',
            }),
          ]),
        ),
      ),
    )
    const user = userEvent.setup()
    renderAt('/projects/p1/brain/e1')
    await user.click(await screen.findByText('Show revision history'))
    // Summary and author share one row, so match the summary as a substring.
    expect(
      await screen.findByText(/brain\(decision\): adopt event sourcing/),
    ).toBeInTheDocument()
    expect(screen.getByText(/agent_historian/)).toBeInTheDocument()
  })
})
