import type { FineTuneStage } from '@/api/endpoints/fine-tuning'
import type { FineTuningErrors, ListPagination } from './types'

/** All valid fine-tune stage values for runtime validation of WS payloads. */
export const VALID_STAGES: ReadonlySet<string> = new Set<FineTuneStage>([
  'idle',
  'generating_data',
  'mining_negatives',
  'training',
  'evaluating',
  'deploying',
  'complete',
  'failed',
])

export const NO_ERRORS: FineTuningErrors = {
  status: null,
  checkpoints: null,
  runs: null,
}

export const NO_MORE: ListPagination = { nextCursor: null, hasMore: false }

// Page size used when draining ``listCheckpoints`` / ``listRuns``.
// Matches the endpoint default; smaller pages only multiply
// round-trips without changing the draining outcome.
export const LIST_PAGE_SIZE = 50

// Safety stop so a backend bug that keeps returning ``has_more=true``
// cannot lock the dashboard in an infinite drain loop.
export const DRAIN_PAGE_LIMIT = 50

/**
 * Pick the first non-null error from the per-resource map for a
 * single banner string. When several resources fail concurrently
 * the page surfaces status > checkpoints > runs in priority order;
 * per-resource detail is still available on ``state.errors`` for
 * finer-grained UI.
 */
export function selectFineTuningBannerError(
  errors: FineTuningErrors,
): string | null {
  return errors.status ?? errors.checkpoints ?? errors.runs ?? null
}
