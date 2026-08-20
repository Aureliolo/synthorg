import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router'
import { describe, expect, it } from 'vitest'

import type { Project } from '@/api/types/projects'
import { apiError } from '@/mocks/handlers'
import { ProjectDeleteAction } from '@/pages/projects/ProjectDeleteAction'
import { ROUTES } from '@/router/routes'
import { useProjectsStore } from '@/stores/projects'
import { server } from '@/test-setup'

import { makeProject } from '../../helpers/factories'

function renderAction(project: Project) {
  useProjectsStore.setState({ projects: [project], selectedProject: project })
  return render(
    <MemoryRouter>
      <ProjectDeleteAction project={project} />
    </MemoryRouter>,
  )
}

async function confirmDelete() {
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: /delete project/i }))
  const dialog = await screen.findByRole('alertdialog')
  await user.click(within(dialog).getByRole('button', { name: /delete project/i }))
}

describe('ProjectDeleteAction', () => {
  it('deletes the project the page is about', async () => {
    // The list view could always delete a selection; a project opened on its
    // own page had no exit at all.
    let deleted = false
    server.use(
      http.delete('/api/v1/projects/:id', () => {
        deleted = true
        return new HttpResponse(null, { status: 204 })
      }),
    )
    renderAction(makeProject('proj-1'))

    await confirmDelete()

    await waitFor(() => {
      expect(deleted).toBe(true)
    })
    expect(useProjectsStore.getState().projects).toEqual([])
  })

  it('takes the operator off the page it just deleted', async () => {
    // The other half of giving this page an exit: staying put leaves an
    // operator looking at a project that no longer exists, and every read the
    // page makes from here answers 404.
    server.use(
      http.delete(
        '/api/v1/projects/:id',
        () => new HttpResponse(null, { status: 204 }),
      ),
    )
    const project = makeProject('proj-1')
    useProjectsStore.setState({ projects: [project], selectedProject: project })
    render(
      <MemoryRouter initialEntries={[`/projects/${project.id}`]}>
        <Routes>
          <Route
            path="/projects/:projectId"
            element={<ProjectDeleteAction project={project} />}
          />
          <Route path={ROUTES.PROJECTS} element={<div>All projects</div>} />
        </Routes>
      </MemoryRouter>,
    )

    await confirmDelete()

    expect(await screen.findByText('All projects')).toBeInTheDocument()
  })

  it('keeps the dialog open when the API refuses', async () => {
    server.use(
      http.delete('/api/v1/projects/:id', () =>
        HttpResponse.json(apiError('Project has live work'), { status: 409 }),
      ),
    )
    renderAction(makeProject('proj-1'))

    await confirmDelete()

    // Read beside the action that caused it, rather than on a page that has
    // already navigated away.
    await waitFor(() => {
      expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    })
    expect(useProjectsStore.getState().projects).toHaveLength(1)
  })

  it('says the workspace goes too, because it does', async () => {
    // Deleting a project now removes the tree its agents wrote into, and an
    // irreversible action has to name everything it takes.
    const user = userEvent.setup()
    renderAction(makeProject('proj-1'))

    await user.click(screen.getByRole('button', { name: /delete project/i }))

    expect(await screen.findByText(/workspace/i)).toBeInTheDocument()
  })
})
