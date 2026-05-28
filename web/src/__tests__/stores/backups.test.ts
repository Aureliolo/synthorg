import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import { listBackups } from '@/api/endpoints/backup'
import { useBackupsStore } from '@/stores/backups'
import { useToastStore } from '@/stores/toast'
import { apiError, successFor, voidSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
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

describe('useBackupsStore', () => {
  beforeEach(() => {
    useBackupsStore.setState({ backups: [], loading: false, error: null, mutating: false })
    useToastStore.getState().dismissAll()
  })

  it('fetches the backup inventory', async () => {
    seedList([buildBackup()])

    await useBackupsStore.getState().fetchBackups()

    expect(useBackupsStore.getState().backups).toHaveLength(1)
    expect(useBackupsStore.getState().loading).toBe(false)
  })

  it('records an error message when the list call fails', async () => {
    server.use(
      http.get('/api/v1/admin/backups', () =>
        HttpResponse.json(apiError('Network down'), { status: 500 }),
      ),
    )

    await useBackupsStore.getState().fetchBackups()

    expect(useBackupsStore.getState().error).toBeTruthy()
  })

  it('creates a backup, refreshes the list, and toasts success', async () => {
    seedList([buildBackup()])

    const ok = await useBackupsStore.getState().createBackup()

    expect(ok).toBe(true)
    expect(useBackupsStore.getState().backups).toHaveLength(1)
    expect(useToastStore.getState().toasts[0]!.title).toBe('Backup created')
  })

  it('optimistically removes a backup on delete and keeps it removed on success', async () => {
    useBackupsStore.setState({
      backups: [buildBackup({ backup_id: 'a' }), buildBackup({ backup_id: 'b' })],
    })
    server.use(
      http.delete('/api/v1/admin/backups/:id', () => HttpResponse.json(voidSuccess())),
    )

    const ok = await useBackupsStore.getState().deleteBackup('a')

    expect(ok).toBe(true)
    expect(useBackupsStore.getState().backups.map((b) => b.backup_id)).toEqual(['b'])
    expect(useToastStore.getState().toasts[0]!.title).toBe('Backup deleted')
  })

  it('rolls back and toasts an error on delete failure', async () => {
    useBackupsStore.setState({ backups: [buildBackup({ backup_id: 'a' })] })
    server.use(
      http.delete('/api/v1/admin/backups/:id', () =>
        HttpResponse.json(apiError('boom')),
      ),
    )

    const ok = await useBackupsStore.getState().deleteBackup('a')

    expect(ok).toBe(false)
    expect(useBackupsStore.getState().backups).toHaveLength(1)
    expect(useToastStore.getState().toasts[0]!.title).toBe('Failed to delete backup')
  })

  it('restores a backup and surfaces the safety backup in the toast', async () => {
    seedList([])

    const ok = await useBackupsStore.getState().restoreBackup('backup-1')

    expect(ok).toBe(true)
    const toast = useToastStore.getState().toasts[0]!
    expect(toast.variant).toBe('success')
    expect(toast.title).toBe('Backup restored')
    expect(toast.description).toContain('backup-safety')
  })
})
