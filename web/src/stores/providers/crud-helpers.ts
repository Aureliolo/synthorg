import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { useToastStore } from '@/stores/toast'
import type { ProvidersGet, ProvidersSet } from './types'

let _mutationCount = 0

export function beginMutation(set: ProvidersSet): void {
  _mutationCount++
  set({ mutating: true })
}

export function endMutation(set: ProvidersSet): void {
  _mutationCount = Math.max(0, _mutationCount - 1)
  if (_mutationCount === 0) set({ mutating: false })
}

export function emitErrorToast(
  err: unknown,
  fallbackTitle: string,
): void {
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(err, fallbackTitle),
    description: getErrorMessage(err),
  })
}

export function emitPlainErrorToast(title: string, err: unknown): void {
  useToastStore.getState().add({
    variant: 'error',
    title,
    description: getErrorMessage(err),
  })
}

export function emitSuccessToast(title: string, description?: string): void {
  useToastStore.getState().add({ variant: 'success', title, description })
}

export async function refreshActiveDetail(
  get: ProvidersGet,
  name: string,
): Promise<void> {
  if (get().selectedProvider?.name === name) {
    await get().fetchProviderDetail(name)
  }
}
