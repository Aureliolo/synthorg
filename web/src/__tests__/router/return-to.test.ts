import { describe, expect, it } from 'vitest'

import { RETURN_TO_PARAM, readReturnTo, wasInterrupted } from '@/router/return-to'

/**
 * A session expired about 50 minutes into a live run, with a decomposition
 * still in flight and no backend auth failure to explain it. The page the
 * operator was watching was replaced by a login screen that looked like a
 * fresh visit, said nothing about why, and had forgotten where they had been.
 *
 * The destination reaches the login screen from a URL, so it is
 * attacker-supplied by construction: these cover the return AND the refusals
 * that stop the fix from turning a credential prompt into an open redirect.
 */

function search(next: string): string {
  return `?${RETURN_TO_PARAM}=${encodeURIComponent(next)}`
}

describe('readReturnTo', () => {
  it('returns the operator to where they were', () => {
    expect(readReturnTo(search('/plans/abc'))).toBe('/plans/abc')
  })

  it('keeps the query string of the page they were on', () => {
    // A filtered list or a paged table is a different page from its bare
    // route, and returning to the bare one loses what they were looking at.
    expect(readReturnTo(search('/tasks?status=blocked'))).toBe('/tasks?status=blocked')
  })

  it('falls back to the dashboard when nothing was carried', () => {
    expect(readReturnTo('')).toBe('/')
  })

  it('refuses an absolute URL', () => {
    // The classic way a credential prompt gets rehosted somewhere else.
    expect(readReturnTo(search('https://elsewhere.example/steal'))).toBe('/')
  })

  it('refuses a scheme-relative host', () => {
    // `//host` is an absolute URL to a browser, and it starts with a slash.
    expect(readReturnTo(search('//elsewhere.example/steal'))).toBe('/')
  })

  it('refuses a backslash the browser would normalise into a slash', () => {
    expect(readReturnTo(search('/\\elsewhere.example'))).toBe('/')
  })

  it('refuses to return to the login screen itself', () => {
    // Signing in only to be shown the sign-in page is a loop, not a return.
    expect(readReturnTo(search('/login'))).toBe('/')
  })

  it('refuses to return to setup', () => {
    expect(readReturnTo(search('/setup'))).toBe('/')
  })

  it('refuses the login screen even with a query string on it', () => {
    expect(readReturnTo(search('/login?next=/plans'))).toBe('/')
  })
})

describe('wasInterrupted', () => {
  it('is true when a session ended somewhere', () => {
    expect(wasInterrupted(search('/plans/abc'))).toBe(true)
  })

  it('is false for somebody who navigated to the login page themselves', () => {
    // They know why they are here, and telling them their session ended
    // would be inventing an event.
    expect(wasInterrupted('')).toBe(false)
  })

  it('is true even for a destination that will be refused', () => {
    // The session still ended. Refusing the destination decides WHERE they
    // land, not whether they were interrupted.
    expect(wasInterrupted(search('https://elsewhere.example'))).toBe(true)
  })
})
