/**
 * Admin backup-inventory store.
 *
 * Owns the toast / error UX for the backup admin endpoints (list,
 * create, delete, restore) so {@link AdminBackupsPage} stays
 * presentational. Follows the canonical store error contract
 * (try/catch -> log + toast -> sentinel return); list reads set
 * `error` instead of toasting. Callers MUST NOT wrap these in try/catch.
 *
 * Create and restore both mutate the server-side inventory (restore
 * also mints a safety backup), so each refreshes the list afterwards
 * rather than guessing the new row shape.
 */

import { create, type StoreApi } from 'zustand'
import {
  createBackup as apiCreate,
  deleteBackup as apiDelete,
  listBackups as apiList,
  restoreBackup as apiRestore,
} from '@/api/endpoints/backup'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import type { BackupInfo } from '@/api/types/backup'

const log = createLogger('backups')

interface BackupsState {
  backups: BackupInfo[]
  loading: boolean
  loadingMore: boolean
  error: string | null
  mutating: boolean
  nextCursor: string | null
  hasMore: boolean
  fetchBackups: () => Promise<void>
  fetchMoreBackups: () => Promise<void>
  createBackup: () => Promise<boolean>
  deleteBackup: (backupId: string) => Promise<boolean>
  restoreBackup: (backupId: string) => Promise<boolean>
}

type BackupsSet = StoreApi<BackupsState>['setState']
type BackupsGet = StoreApi<BackupsState>['getState']

async function fetchBackupsImpl(set: BackupsSet): Promise<void> {
  set({ loading: true, error: null })
  try {
    const page = await apiList()
    set({
      backups: [...page.data],
      nextCursor: page.nextCursor,
      hasMore: page.hasMore,
      loading: false,
    })
  } catch (err) {
    log.error('Failed to fetch backups:', getErrorMessage(err))
    set({ error: getErrorMessage(err), loading: false })
  }
}

async function fetchMoreBackupsImpl(set: BackupsSet, get: BackupsGet): Promise<void> {
  const { hasMore, nextCursor, loadingMore } = get()
  if (!hasMore || !nextCursor || loadingMore) return
  set({ loadingMore: true })
  try {
    const page = await apiList({ cursor: nextCursor })
    set((s) => ({
      backups: [...s.backups, ...page.data],
      nextCursor: page.nextCursor,
      hasMore: page.hasMore,
      loadingMore: false,
    }))
  } catch (err) {
    log.error('Failed to fetch more backups:', getErrorMessage(err))
    set({ error: getErrorMessage(err), loadingMore: false })
  }
}

async function createBackupImpl(set: BackupsSet): Promise<boolean> {
  set({ mutating: true })
  try {
    await apiCreate()
    useToastStore.getState().add({ variant: 'success', title: 'Backup created' })
    await fetchBackupsImpl(set)
    set({ mutating: false })
    return true
  } catch (err) {
    set({ mutating: false })
    log.error('Create backup failed:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to create backup'),
      description: getErrorMessage(err),
    })
    return false
  }
}

async function deleteBackupImpl(
  set: BackupsSet,
  get: BackupsGet,
  backupId: string,
): Promise<boolean> {
  const before = get().backups
  const removed = before.find((b) => b.backup_id === backupId) ?? null
  set({ backups: before.filter((b) => b.backup_id !== backupId), mutating: true })
  try {
    await apiDelete(backupId)
    set({ mutating: false })
    useToastStore.getState().add({ variant: 'success', title: 'Backup deleted' })
    return true
  } catch (err) {
    const current = get().backups
    const alreadyBack = current.some((b) => b.backup_id === backupId)
    set({
      mutating: false,
      backups: !alreadyBack && removed ? [removed, ...current] : current,
    })
    log.error('Delete backup failed:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to delete backup'),
      description: getErrorMessage(err),
    })
    return false
  }
}

async function restoreBackupImpl(set: BackupsSet, backupId: string): Promise<boolean> {
  set({ mutating: true })
  try {
    const result = await apiRestore({ backup_id: backupId, confirm: true })
    useToastStore.getState().add({
      variant: 'success',
      title: 'Backup restored',
      description: result.restart_required
        ? `A restart is required to apply the restore. Safety backup ${result.safety_backup_id} was created.`
        : `Safety backup ${result.safety_backup_id} was created.`,
    })
    await fetchBackupsImpl(set)
    set({ mutating: false })
    return true
  } catch (err) {
    set({ mutating: false })
    log.error('Restore backup failed:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to restore backup'),
      description: getErrorMessage(err),
    })
    return false
  }
}

export const useBackupsStore = create<BackupsState>()((set, get) => ({
  backups: [],
  loading: false,
  loadingMore: false,
  error: null,
  mutating: false,
  nextCursor: null,
  hasMore: false,
  fetchBackups: () => fetchBackupsImpl(set),
  fetchMoreBackups: () => fetchMoreBackupsImpl(set, get),
  createBackup: () => createBackupImpl(set),
  deleteBackup: (backupId) => deleteBackupImpl(set, get, backupId),
  restoreBackup: (backupId) => restoreBackupImpl(set, backupId),
}))
