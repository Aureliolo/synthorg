import { apiClient, unwrap, unwrapVoid } from '../client'
import type { BackupInfo, BackupManifest, RestoreRequest, RestoreResponse } from '../types/backup'
import type { ApiResponse } from '../types/http'

export async function createBackup(idempotencyKey?: string): Promise<BackupManifest> {
  // The backend requires the Idempotency-Key header on POST
  // /admin/backups so a 5xx-driven retry cannot launch concurrent
  // backups and violate the at-most-one-running invariant. Callers
  // may supply their own key (recommended for retry semantics);
  // otherwise we mint a fresh UUID per call so first-time submissions
  // still satisfy the contract without forcing every caller to think
  // about it.
  const key = idempotencyKey ?? crypto.randomUUID()
  const response = await apiClient.post<ApiResponse<BackupManifest>>(
    '/admin/backups',
    null,
    { headers: { 'Idempotency-Key': key } },
  )
  return unwrap(response)
}

export async function listBackups(): Promise<BackupInfo[]> {
  const response = await apiClient.get<ApiResponse<BackupInfo[]>>('/admin/backups')
  return unwrap(response)
}

export async function getBackup(backupId: string): Promise<BackupManifest> {
  const response = await apiClient.get<ApiResponse<BackupManifest>>(`/admin/backups/${encodeURIComponent(backupId)}`)
  return unwrap(response)
}

export async function deleteBackup(backupId: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(`/admin/backups/${encodeURIComponent(backupId)}`)
  unwrapVoid(response)
}

export async function restoreBackup(data: RestoreRequest): Promise<RestoreResponse> {
  const response = await apiClient.post<ApiResponse<RestoreResponse>>('/admin/backups/restore', data)
  return unwrap(response)
}
