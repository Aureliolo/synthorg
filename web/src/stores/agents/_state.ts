// Track the latest requested agent id so stale fetches don't
// overwrite a fresher detail load.
let _detailRequestId = ''

export function setDetailRequestId(agentId: string): void {
  _detailRequestId = agentId
}

export function getDetailRequestId(): string {
  return _detailRequestId
}

export function clearDetailRequestId(): void {
  _detailRequestId = ''
}

export const MAX_ACTIVITIES = 100
