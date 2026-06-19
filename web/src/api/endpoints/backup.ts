import { apiClient, unwrap, unwrapPaginated, unwrapVoid, type PaginatedResult } from '../client'
import { idempotencyKeyHeader } from '../idempotency'
import type { BackupInfo, BackupManifest, RestoreRequest, RestoreResponse } from '../types/backup'
import type { ApiResponse, PaginatedResponse } from '../types/http'

export async function createBackup(idempotencyKey?: string): Promise<BackupManifest> {
  // The backend requires the Idempotency-Key header on POST /admin/backups
  // so a 5xx-driven retry cannot launch concurrent backups and violate the
  // at-most-one-running invariant.
  const response = await apiClient.post<ApiResponse<BackupManifest>>(
    '/admin/backups',
    null,
    { headers: idempotencyKeyHeader(idempotencyKey) },
  )
  return unwrap(response)
}

export async function listBackups(params?: {
  /** Opaque pagination cursor from the previous response's `pagination.next_cursor`. */
  cursor?: string | null
  limit?: number
}): Promise<PaginatedResult<BackupInfo>> {
  const response = await apiClient.get<PaginatedResponse<BackupInfo>>('/admin/backups', {
    params,
  })
  return unwrapPaginated<BackupInfo>(response)
}

export async function getBackup(backupId: string): Promise<BackupManifest> {
  const response = await apiClient.get<ApiResponse<BackupManifest>>(`/admin/backups/${encodeURIComponent(backupId)}`)
  return unwrap(response)
}

export async function deleteBackup(backupId: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(`/admin/backups/${encodeURIComponent(backupId)}`)
  unwrapVoid(response)
}

export async function restoreBackup(
  data: RestoreRequest,
  idempotencyKey?: string,
): Promise<RestoreResponse> {
  // The backend requires the Idempotency-Key header on restore (destructive)
  // so a 5xx-driven retry cannot re-run the restore over the same data.
  const response = await apiClient.post<ApiResponse<RestoreResponse>>(
    '/admin/backups/restore',
    data,
    { headers: idempotencyKeyHeader(idempotencyKey) },
  )
  return unwrap(response)
}
