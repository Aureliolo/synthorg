import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type { CompanyConfig } from '@/api/types/org'
import type { CompanyGet, CompanySet } from './types'

export const log = createLogger('company')

export function beginMutation(set: CompanySet): void {
  set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
}

export function endMutation(set: CompanySet, withError?: string): void {
  if (withError === undefined) {
    set((s) => ({ savingCount: Math.max(0, s.savingCount - 1) }))
  } else {
    set((s) => ({
      savingCount: Math.max(0, s.savingCount - 1),
      saveError: withError,
    }))
  }
}

export function emitErrorToast(
  err: unknown,
  fallbackTitle: string,
  logPrefix: string,
): void {
  log.error(`${logPrefix}:`, sanitizeForLog(err))
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(err, fallbackTitle),
    description: getErrorMessage(err),
  })
}

export function emitSuccessToast(title: string): void {
  useToastStore.getState().add({ variant: 'success', title })
}

/**
 * Apply a patch to ``config`` only when a config currently exists.
 * Wraps the canonical pattern ``prev ? { config: { ...prev, ... } } : {}``
 * so individual CRUD callers stay focused on the entity transform.
 */
export function patchConfig(
  get: CompanyGet,
  transform: (prev: CompanyConfig) => CompanyConfig,
): Partial<{ config: CompanyConfig }> {
  const prev = get().config
  return prev ? { config: transform(prev) } : {}
}
