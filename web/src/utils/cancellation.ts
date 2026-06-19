/**
 * One-way cancellation token for effects and bounded async loops.
 *
 * ``cancelled()`` is a function rather than a bare flag on purpose: a stale
 * control-flow narrowing after an ``await`` cannot mask a ``cancel()`` that
 * ran in an effect-cleanup or sibling closure, because the call always reads
 * the live value. Conceptually analogous to ``AbortSignal`` but exposes the
 * cancellation state as a function call rather than a boolean property (and
 * without the event-listener machinery), which is what keeps it opaque to the
 * control-flow analysis.
 */
export interface CancellationToken {
  /** True once {@link CancellationToken.cancel} has been called. */
  cancelled: () => boolean
  /** Mark the token cancelled. Idempotent. */
  cancel: () => void
}

/** Create a fresh, uncancelled {@link CancellationToken}. */
export function createCancellationToken(): CancellationToken {
  let cancelled = false
  return {
    cancelled: () => cancelled,
    cancel: () => {
      cancelled = true
    },
  }
}
