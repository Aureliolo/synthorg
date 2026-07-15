let _detailRequestToken = 0
let _listRequestToken = 0
let _autonomyModeRequestToken = 0

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

export function nextAutonomyModeRequestToken(): number {
  _autonomyModeRequestToken += 1
  return _autonomyModeRequestToken
}

export function isStaleAutonomyModeRequest(token: number): boolean {
  return _autonomyModeRequestToken !== token
}
