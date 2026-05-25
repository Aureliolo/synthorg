let channelRequestSeq = 0
let messageRequestSeq = 0
let activityRequestSeq = 0

export function nextChannelRequestSeq(): number {
  channelRequestSeq += 1
  return channelRequestSeq
}

export function getChannelRequestSeq(): number {
  return channelRequestSeq
}

export function nextMessageRequestSeq(): number {
  messageRequestSeq += 1
  return messageRequestSeq
}

export function getMessageRequestSeq(): number {
  return messageRequestSeq
}

export function nextActivityRequestSeq(): number {
  activityRequestSeq += 1
  return activityRequestSeq
}

export function getActivityRequestSeq(): number {
  return activityRequestSeq
}

/** Reset module-level sequence counters -- test-only. */
export function _resetRequestSeqs(): void {
  channelRequestSeq = 0
  messageRequestSeq = 0
  activityRequestSeq = 0
}
