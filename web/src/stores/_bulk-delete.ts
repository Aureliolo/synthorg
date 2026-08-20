/**
 * One call, one toast, for every list that deletes a selection.
 *
 * Each of these endpoints is rate limited per user, so a client loop over N
 * rows refused its own tail for a reason that had nothing to do with the rows:
 * the backend takes the whole selection in one request and answers per row.
 *
 * The toast lives here rather than in each store because a partial outcome is
 * neither a success nor a failure, and three copies of that decision is how one
 * list comes to report "failed" for an action that half worked.
 */

import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { formatBatchErrors, getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import type { BulkDeleteResult } from '@/api/types/bulk-delete'

const log = createLogger('bulk-delete')

/** What the caller reports back to its page. */
export interface BulkDeleteOutcome {
  succeeded: number
  failed: number
  failedReasons: string[]
}

/** The noun this list deletes, singular and plural. */
export interface BulkDeleteNoun {
  readonly one: string
  readonly many: string
}

export interface BulkDeleteArgs {
  readonly ids: readonly string[]
  /** The single request that does the deleting. */
  readonly call: (ids: readonly string[]) => Promise<BulkDeleteResult>
  /** Drop the rows the backend removed from the list on screen. */
  readonly removeRows: (ids: readonly string[]) => void
  readonly noun: BulkDeleteNoun
}

function emitToast(
  outcome: BulkDeleteOutcome,
  total: number,
  noun: BulkDeleteNoun,
): void {
  const description = outcome.failedReasons.length > 0
    ? formatBatchErrors(outcome.failedReasons)
    : undefined
  if (outcome.failed === 0 && outcome.succeeded > 0) {
    useToastStore.getState().add({
      variant: 'success',
      title: outcome.succeeded === 1
        ? `${noun.one} deleted`
        : `${String(outcome.succeeded)} ${noun.many} deleted`,
    })
    return
  }
  if (outcome.succeeded > 0) {
    useToastStore.getState().add({
      variant: 'warning',
      title: `Deleted ${String(outcome.succeeded)} of ${String(total)} ${noun.many}`,
      description,
    })
    return
  }
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(
      new Error(outcome.failedReasons[0] ?? `Failed to delete ${noun.many}`),
      outcome.failed === 1
        ? `Failed to delete ${noun.one.toLowerCase()}`
        : `Failed to delete ${String(outcome.failed)} ${noun.many}`,
    ),
    description,
  })
}

/**
 * Delete *ids* in one request and report what happened.
 *
 * Rows are removed AFTER the answer rather than optimistically: the backend
 * says which ones went, so there is nothing to guess at and nothing to roll
 * back. Returns `false` when the request itself failed, which is the sentinel
 * the store-mutation contract uses for a call that settled nothing.
 */
export async function runBulkDelete({
  ids,
  call,
  removeRows,
  noun,
}: BulkDeleteArgs): Promise<BulkDeleteOutcome | false> {
  const uniqueIds = Array.from(new Set(ids))
  // The guard covers the whole body, not just the call. Everything after it is
  // caller-supplied (the row removal each store passes in) or another store's
  // (the toast), so a throw there would escape a store mutation, which the
  // contract forbids, and strand the confirm dialog its caller cannot close
  // while a delete is in flight.
  try {
    const result: BulkDeleteResult = await call(uniqueIds)
    removeRows(result.deleted)
    const outcome: BulkDeleteOutcome = {
      succeeded: result.deleted.length,
      failed: result.failed.length,
      failedReasons: result.failed.map((failure) => failure.reason),
    }
    if (outcome.failed > 0) {
      log.error(`Bulk delete ${noun.many} partial`, sanitizeForLog({
        failed: outcome.failed,
        reasons: outcome.failedReasons,
      }))
    }
    emitToast(outcome, uniqueIds.length, noun)
    return outcome.succeeded === 0 && outcome.failed > 0 ? false : outcome
  } catch (err) {
    log.error(`Bulk delete ${noun.many} failed`, sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, `Failed to delete ${noun.many}`),
      description: getErrorMessage(err),
    })
    return false
  }
}
