// Page size for the cursor-paginated workflows list. Centralised
// so the initial fetch and load-more agree on the same limit (the
// cursor-pagination contract requires it on every fetch-more call).
export const WORKFLOWS_PAGE_LIMIT = 200

let _listRequestToken = 0
let _blueprintRequestToken = 0

export function nextListRequestToken(): number {
  _listRequestToken += 1
  return _listRequestToken
}

export function getListRequestToken(): number {
  return _listRequestToken
}

export function isStaleListRequest(token: number): boolean {
  return _listRequestToken !== token
}

export function nextBlueprintRequestToken(): number {
  _blueprintRequestToken += 1
  return _blueprintRequestToken
}

export function isStaleBlueprintRequest(token: number): boolean {
  return _blueprintRequestToken !== token
}
