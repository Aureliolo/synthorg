import { http, HttpResponse } from 'msw'
import type {
  createBackup,
  getBackup,
  listBackups,
  restoreBackup,
} from '@/api/endpoints/backup'
import type { BackupManifest } from '@/api/types/backup'
import { emptyPage, paginatedFor, successFor, voidSuccess } from './helpers'
import type { BackupInfo } from '@/api/types/backup'

function buildManifest(overrides: Partial<BackupManifest> = {}): BackupManifest {
  return {
    backup_id: 'backup-default',
    synthorg_version: '0.6.4',
    timestamp: '2026-04-19T00:00:00Z',
    trigger: 'manual',
    components: ['persistence'],
    size_bytes: 0,
    checksum: 'sha256:0',
    ...overrides,
  }
}

export const backupHandlers = [
  http.post('/api/v1/admin/backups', () =>
    HttpResponse.json(successFor<typeof createBackup>(buildManifest())),
  ),
  http.get('/api/v1/admin/backups', () =>
    HttpResponse.json(paginatedFor<typeof listBackups>(emptyPage<BackupInfo>())),
  ),
  http.get('/api/v1/admin/backups/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getBackup>(buildManifest({ backup_id: String(params.id) })),
    ),
  ),
  http.delete('/api/v1/admin/backups/:id', () =>
    HttpResponse.json(voidSuccess()),
  ),
  http.post('/api/v1/admin/backups/restore', async ({ request }) => {
    const body = (await request.json()) as { backup_id?: string }
    return HttpResponse.json(
      successFor<typeof restoreBackup>({
        manifest: buildManifest({ backup_id: body.backup_id ?? 'backup-default' }),
        restored_components: ['persistence'],
        safety_backup_id: 'backup-safety',
        restart_required: false,
      }),
    )
  }),
]
