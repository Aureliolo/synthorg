import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'
import AdminBackupsPage from '@/pages/AdminBackupsPage'
import { listBackups } from '@/api/endpoints/backup'
import { apiError, paginatedFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import { useBackupsStore } from '@/stores/backups'
import { useToastStore } from '@/stores/toast'
import type { PaginatedResult } from '@/api/client'
import type { BackupInfo } from '@/api/types/backup'

function buildBackup(overrides: Partial<BackupInfo> = {}): BackupInfo {
  return {
    backup_id: 'backup-1',
    components: ['persistence'],
    compressed: true,
    size_bytes: 1024,
    timestamp: '2026-04-19T00:00:00Z',
    trigger: 'manual',
    ...overrides,
  }
}

function singlePage(backups: BackupInfo[]): PaginatedResult<BackupInfo> {
  const limit = 200
  return {
    data: [...backups],
    limit,
    nextCursor: null,
    hasMore: false,
    pagination: { limit, next_cursor: null, has_more: false },
  }
}

function seedList(backups: BackupInfo[]) {
  server.use(
    http.get('/api/v1/admin/backups', () =>
      HttpResponse.json(paginatedFor<typeof listBackups>(singlePage(backups))),
    ),
  )
}

function renderPage() {
  render(
    <MemoryRouter>
      <AdminBackupsPage />
    </MemoryRouter>,
  )
}

describe('AdminBackupsPage', () => {
  beforeEach(() => {
    useBackupsStore.setState({
      backups: [],
      loading: false,
      loadingMore: false,
      error: null,
      mutating: false,
      nextCursor: null,
      hasMore: false,
    })
    useToastStore.getState().dismissAll()
  })

  it('deletes a backup through a confirmation dialog', async () => {
    const user = userEvent.setup()
    seedList([buildBackup({ backup_id: 'backup-1' })])
    renderPage()

    await screen.findByText('backup-1')
    await user.click(screen.getByRole('button', { name: /delete backup backup-1/i }))

    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('Delete backup')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: /^delete$/i }))

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.title === 'Backup deleted'),
      ).toBe(true)
    })
  })

  it('restores a backup through a confirmation dialog', async () => {
    const user = userEvent.setup()
    seedList([buildBackup({ backup_id: 'backup-1' })])
    renderPage()

    await screen.findByText('backup-1')
    await user.click(screen.getByRole('button', { name: /restore backup backup-1/i }))

    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('Restore backup')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: /^restore$/i }))

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.title === 'Backup restored'),
      ).toBe(true)
    })
  })

  it('keeps the row and surfaces an error toast when delete fails', async () => {
    const user = userEvent.setup()
    seedList([buildBackup({ backup_id: 'backup-1' })])
    renderPage()

    await screen.findByText('backup-1')
    server.use(
      http.delete('/api/v1/admin/backups/:id', () =>
        HttpResponse.json(apiError('boom'), { status: 500 }),
      ),
    )
    await user.click(screen.getByRole('button', { name: /delete backup backup-1/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /^delete$/i }))

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.title === 'Failed to delete backup'),
      ).toBe(true)
    })
    // Optimistic removal rolled back: the row is still rendered.
    expect(screen.getByText('backup-1')).toBeInTheDocument()
  })

  it('creates a backup from the header action', async () => {
    const user = userEvent.setup()
    seedList([])
    renderPage()

    await screen.findByText('No backups yet')
    await user.click(screen.getByRole('button', { name: /create backup/i }))

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.title === 'Backup created'),
      ).toBe(true)
    })
  })
})
