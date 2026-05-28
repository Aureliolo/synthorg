import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'
import AdminBackupsPage from '@/pages/AdminBackupsPage'
import { listBackups } from '@/api/endpoints/backup'
import { successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import { useBackupsStore } from '@/stores/backups'
import { useToastStore } from '@/stores/toast'
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

function seedList(backups: BackupInfo[]) {
  server.use(
    http.get('/api/v1/admin/backups', () =>
      HttpResponse.json(successFor<typeof listBackups>(backups)),
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
    useBackupsStore.setState({ backups: [], loading: false, error: null, mutating: false })
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
