/**
 * Leak helpers for the active-handle tracker regression suite.
 *
 * Each function deliberately creates a tracked active handle and
 * never cleans it up. The functions live in `web/src/__tests__/_infra`
 * (i.e. inside `web/src/`) so the tracker classifies the creation
 * stack as user code via `findUserFrame` and fails the test in
 * `afterEach`. The regression suite drives this by importing each
 * helper from `web/test-infra/active-handle-reporter.fixture.ts`.
 *
 * These helpers are NOT used by application code. They exist to
 * provide a stable user-frame anchor for the tracker tests.
 */

export function leakSetTimeout(): void {
  // A 5-second timeout that the fixture deliberately never clears.
  // The tracker must report this as a Timeout leak attributed to
  // this frame.
  setTimeout(() => { /* never fires within the test */ }, 5_000)
}

export function leakSetInterval(): void {
  // setInterval underlies the same Timeout handle type in Node, so
  // the tracker reports the resource as ``Timeout`` (not a
  // separately tracked ``Interval``). The leak shape is "interval
  // scheduled, never cleared".
  setInterval(() => { /* never fires within the test */ }, 10_000)
}

export function leakChainedSetTimeout(): void {
  // A setTimeout that schedules another setTimeout when it fires.
  // The drain loop in the tracker fires the first one, but the
  // chain is unbounded -- after MAX_DRAIN_ITERATIONS the diff still
  // has a live Timeout, so the test must fail.
  function tick(): void {
    setTimeout(tick, 1_000)
  }
  tick()
}
