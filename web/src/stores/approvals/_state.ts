/**
 * Module-scoped state shared across the approvals package's slice
 * files. Owning these here (rather than in any single action file)
 * keeps the slice files thin and avoids accidental duplicate
 * Set / counter instances if two slices each declared their own.
 */

export const pendingTransitions = new Set<string>()

export const MAX_BATCH_SIZE = 50

let listRequestSeq = 0
let detailRequestSeq = 0
// Generation token bumped by ``dispose()``. Pairs with the seq
// counters so a still-in-flight request from before a dispose can
// never collide with fresh post-dispose seq values (the captured
// epoch always differs from the current epoch after a bump).
let requestEpoch = 0

export function nextListRequestSeq(): number {
  listRequestSeq += 1
  return listRequestSeq
}

export function getListRequestSeq(): number {
  return listRequestSeq
}

export function nextDetailRequestSeq(): number {
  detailRequestSeq += 1
  return detailRequestSeq
}

export function getDetailRequestSeq(): number {
  return detailRequestSeq
}

export function getRequestEpoch(): number {
  return requestEpoch
}

export function bumpRequestEpoch(): void {
  requestEpoch += 1
}

export function resetListRequestSeq(): void {
  listRequestSeq = 0
}

/** Clear module-level pendingTransitions -- test-only. */
export function _resetPendingTransitions(): void {
  pendingTransitions.clear()
}

/** Reset module-level detailRequestSeq -- test-only. */
export function _resetDetailRequestSeq(): void {
  detailRequestSeq = 0
}
