import { fireEvent, render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, it, expect, vi } from 'vitest'
import { successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import { RepoScopePicker } from '@/pages/connections/RepoScopePicker'
import type { scanAccessibleRepos } from '@/api/endpoints/connections'
import type { ForgeAccessibleRepo } from '@/api/types/integrations'

const REPOS: readonly ForgeAccessibleRepo[] = [
  { owner: 'acme', repo: 'web-app', permission: 'admin', private: true },
  { owner: 'acme', repo: 'api-service', permission: 'write', private: false },
]

function useScanHandler(repos: readonly ForgeAccessibleRepo[]) {
  server.use(
    http.get('/api/v1/connections/:name/accessible-repos', () =>
      HttpResponse.json(successFor<typeof scanAccessibleRepos>(repos)),
    ),
  )
}

function renderPicker(selected: readonly string[] = [], onChange = vi.fn()) {
  render(
    <RepoScopePicker connectionName="primary-github" selected={selected} onChange={onChange} />,
  )
  return { onChange }
}

describe('RepoScopePicker', () => {
  it('does not scan until the button is pressed', () => {
    renderPicker()
    expect(screen.queryByLabelText('acme/web-app')).not.toBeInTheDocument()
  })

  it('lists reachable repositories after scanning', async () => {
    useScanHandler(REPOS)
    renderPicker()
    fireEvent.click(screen.getByRole('button', { name: 'Scan repositories' }))
    expect(await screen.findByLabelText('acme/web-app')).toBeInTheDocument()
    expect(screen.getByLabelText('acme/api-service')).toBeInTheDocument()
  })

  it('adds a repository to the selection when ticked', async () => {
    useScanHandler(REPOS)
    const { onChange } = renderPicker(['acme/api-service'])
    fireEvent.click(screen.getByRole('button', { name: 'Scan repositories' }))
    const checkbox = await screen.findByLabelText('acme/web-app')
    fireEvent.click(checkbox)
    expect(onChange).toHaveBeenCalledWith(['acme/api-service', 'acme/web-app'])
  })

  it('removes a repository from the selection when unticked', async () => {
    useScanHandler(REPOS)
    const { onChange } = renderPicker(['acme/web-app', 'acme/api-service'])
    fireEvent.click(screen.getByRole('button', { name: 'Scan repositories' }))
    const checkbox = await screen.findByLabelText('acme/web-app')
    fireEvent.click(checkbox)
    expect(onChange).toHaveBeenCalledWith(['acme/api-service'])
  })

  it('shows an empty-token message when no repos are reachable', async () => {
    useScanHandler([])
    renderPicker()
    fireEvent.click(screen.getByRole('button', { name: 'Scan repositories' }))
    expect(
      await screen.findByText('This token cannot reach any repositories.'),
    ).toBeInTheDocument()
  })

  it('surfaces a scan failure', async () => {
    server.use(
      http.get('/api/v1/connections/:name/accessible-repos', () =>
        HttpResponse.json({ error: { message: 'boom' } }, { status: 403 }),
      ),
    )
    renderPicker()
    fireEvent.click(screen.getByRole('button', { name: 'Scan repositories' }))
    expect(await screen.findByText('Permission denied')).toBeInTheDocument()
  })

  it('summarises the current selection', () => {
    renderPicker(['acme/web-app'])
    expect(screen.getByText(/1 repository in scope: acme\/web-app/)).toBeInTheDocument()
  })
})
