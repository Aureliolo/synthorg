let _detailRequestToken = 0
let _listRequestToken = 0

// Id of the artifact whose detail fetch is currently in-flight.
// ``fetchArtifactDetail`` clears ``selectedArtifact`` *before* awaiting
// the API, so an ``isSelected`` check alone can't invalidate a pending
// detail load when the same artifact is deleted mid-flight. Tracking
// the pending id lets ``deleteArtifact`` bump ``_detailRequestToken``
// and keep stale responses from repopulating deleted data.
let _pendingDetailId: string | null = null

export function nextDetailRequestToken(): number {
  _detailRequestToken += 1
  return _detailRequestToken
}

export function bumpDetailRequestToken(): void {
  _detailRequestToken += 1
}

export function isStaleDetailRequest(token: number): boolean {
  return _detailRequestToken !== token
}

export function nextListRequestToken(): number {
  _listRequestToken += 1
  return _listRequestToken
}

export function bumpListRequestToken(): void {
  _listRequestToken += 1
}

export function isStaleListRequest(token: number): boolean {
  return _listRequestToken !== token
}

export function setPendingDetailId(id: string | null): void {
  _pendingDetailId = id
}

export function getPendingDetailId(): string | null {
  return _pendingDetailId
}
