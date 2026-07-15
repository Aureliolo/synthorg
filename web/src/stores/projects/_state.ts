let _detailRequestToken = 0
let _listRequestToken = 0
// Keyed per project: a latest-wins guard must only invalidate competing
// updates for the SAME initiative, so project A's response is never treated
// as stale because project B was updated after it.
const _autonomyModeRequestTokens = new Map<string, number>()

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

export function isStaleListRequest(token: number): boolean {
  return _listRequestToken !== token
}

export function nextAutonomyModeRequestToken(projectId: string): number {
  const next = (_autonomyModeRequestTokens.get(projectId) ?? 0) + 1
  _autonomyModeRequestTokens.set(projectId, next)
  return next
}

export function isStaleAutonomyModeRequest(
  projectId: string,
  token: number,
): boolean {
  return _autonomyModeRequestTokens.get(projectId) !== token
}
