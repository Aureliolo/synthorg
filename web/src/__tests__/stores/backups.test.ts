import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import { listBackups, restoreBackup } from '@/api/endpoints/backup'
import { useBackupsStore } from '@/stores/backups'
import { useToastStore } from '@/stores/toast'
import { apiError, paginatedFor, successFor, voidSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
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

describe('useBackupsStore', () => {
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

  it('fetches the backup inventory', async () => {
    seedList([buildBackup()])

    await useBackupsStore.getState().fetchBackups()

    expect(useBackupsStore.getState().backups).toHaveLength(1)
    expect(useBackupsStore.getState().loading).toBe(false)
  })

  it('appends the next page on fetchMoreBackups', async () => {
    useBackupsStore.setState({
      backups: [buildBackup({ backup_id: 'a' })],
      hasMore: true,
      nextCursor: 'cursor-1',
    })
    server.use(
      http.get('/api/v1/admin/backups', () =>
        HttpResponse.json(
          paginatedFor<typeof listBackups>(singlePage([buildBackup({ backup_id: 'b' })])),
        ),
      ),
    )

    await useBackupsStore.getState().fetchMoreBackups()

    const state = useBackupsStore.getState()
    expect(state.backups.map((b) => b.backup_id)).toEqual(['a', 'b'])
    expect(state.hasMore).toBe(false)
  })

  it('records an error message when the list call fails', async () => {
    server.use(
      http.get('/api/v1/admin/backups', () =>
        HttpResponse.json(apiError('Network down'), { status: 500 }),
      ),
    )

    await useBackupsStore.getState().fetchBackups()

    expect(typeof useBackupsStore.getState().error).toBe('string')
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
        HttpResponse.json(apiError('boom'), { status: 500 }),
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

  it('surfaces the restart-required notice when restore reports restart_required', async () => {
    seedList([])
    server.use(
      http.post('/api/v1/admin/backups/restore', () =>
        HttpResponse.json(
          successFor<typeof restoreBackup>({
            manifest: {
              backup_id: 'backup-1',
              checksum: 'sha256:0',
              components: ['persistence'],
              size_bytes: 0,
              synthorg_version: '0.0.0',
              timestamp: '2026-04-19T00:00:00Z',
              trigger: 'manual',
            },
            restored_components: ['persistence'],
            safety_backup_id: 'backup-safety',
            restart_required: true,
          }),
        ),
      ),
    )

    await useBackupsStore.getState().restoreBackup('backup-1')

    const toast = useToastStore.getState().toasts[0]!
    expect(toast.description).toContain('restart is required')
  })
})
