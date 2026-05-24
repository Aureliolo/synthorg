// Track the latest requested agent name so stale fetches don't
// overwrite a fresher detail load.
let _detailRequestName = ''

export function setDetailRequestName(name: string): void {
  _detailRequestName = name
}

export function getDetailRequestName(): string {
  return _detailRequestName
}

export function clearDetailRequestName(): void {
  _detailRequestName = ''
}

export const MAX_ACTIVITIES = 100
