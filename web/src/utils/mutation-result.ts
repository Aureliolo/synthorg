/**
 * MutationResult: opt-in discriminated union for store mutation
 * return values (M5).
 *
 * The dashboard's canonical store mutation contract returns a sentinel
 * (``null`` for entity returns, ``false`` for boolean returns) on
 * failure. Sentinels are cheap to consume but TypeScript cannot enforce
 * caller branching: a forgotten ``if (result === null)`` compiles
 * cleanly and silently dereferences ``.name`` on a null at runtime.
 *
 * ``MutationResult<T>`` lets new mutations opt into a stricter
 * contract: ``{ ok: true; value: T }`` on success and
 * ``{ ok: false; error?: string }`` on failure. The discriminant
 * forces every caller to branch on ``result.ok`` before reaching
 * either ``result.value`` or ``result.error``, with the rest of the
 * union narrowed away by the compiler.
 *
 * Existing mutations stay on the sentinel-return contract so the
 * migration can land per-mutation without one big API churn. New
 * mutations should default to ``MutationResult`` and the helper
 * factories below to keep error-toast UX uniform.
 */

import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

export type MutationResult<T = void> =
  | { ok: true; value: T }
  | { ok: false; error: string }

export function mutationOk<T>(value: T): MutationResult<T> {
  return { ok: true, value }
}

export function mutationFailed(error: string): MutationResult<never> {
  return { ok: false, error }
}

/**
 * Centralise the "log + toast + sentinel" failure path so per-mutation
 * catch blocks stop drifting on toast title / error formatting.
 *
 * Returns a ``MutationResult<never>`` failure envelope; callers can
 * unwrap with ``if (!result.ok)`` and surface the error message
 * inline (e.g. a form-level banner) without re-emitting a toast.
 */
export function failMutation(
  err: unknown,
  fallbackTitle: string,
): MutationResult<never> {
  const message = getErrorMessage(err)
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(err, fallbackTitle),
    description: message,
  })
  return { ok: false, error: message }
}
