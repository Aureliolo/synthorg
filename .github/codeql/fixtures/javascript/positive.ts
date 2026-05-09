// Positive fixtures: deliberate genuine leaks with no sanitiser.
//
// CodeQL analysis MUST still alert on these functions even with the
// synthorg-sanitisers extension pack loaded. If alerts do not fire, the
// pack is over-suppressing.

export function positiveLogInjection(userInput: string): void {
  // js/log-injection MUST fire here. Raw string, no sanitiser.
  console.warn('user said:', userInput)
}

export function positiveWsLogInjection(wsPayload: string): void {
  // js/log-injection MUST fire here. WS payload flows raw to console.
  console.error('ws frame:', wsPayload)
}
