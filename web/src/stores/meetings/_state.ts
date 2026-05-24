let listRequestSeq = 0
let detailRequestSeq = 0
// Generation token bumped by ``dispose()``. The seq counters reset
// to zero on dispose so a new fetch starts fresh, but a still-in-flight
// request from before the dispose could otherwise win the
// ``seq === current`` check by collision (its captured seq could
// match a fresh post-dispose seq value). Pairing seq with the epoch
// makes that scenario impossible: a stale request always sees a
// different ``epoch`` and short-circuits.
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

/** Reset module-level request seq counters -- test-only. */
export function _resetRequestSeqs(): void {
  listRequestSeq = 0
  detailRequestSeq = 0
}
