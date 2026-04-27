/**
 * CodSpeed bench for the pure CSRF cookie parser.
 *
 * Benches ``parseCsrfTokenFromCookieString`` (the pure parser) rather
 * than ``getCsrfToken`` (the DOM wrapper) so timings reflect parsing
 * cost only, not jsdom's tough-cookie shim or any test-setup state.
 * Per ``web/CLAUDE.md`` -- bench targets must be pure-compute helpers.
 */
import { bench, describe } from 'vitest'

import { parseCsrfTokenFromCookieString } from '@/utils/csrf'

const SINGLE_COOKIE = 'csrf_token=test-csrf-token'
const MULTIPLE_COOKIES_DEEP =
  'pad_a=1; pad_b=2; pad_c=3; csrf_token=test-csrf-token; pad_d=4'

describe('CSRF cookie parser', () => {
  bench('parseCsrfTokenFromCookieString x500 (single cookie)', () => {
    for (let i = 0; i < 500; i++) {
      parseCsrfTokenFromCookieString(SINGLE_COOKIE)
    }
  })

  bench('parseCsrfTokenFromCookieString x500 (multiple cookies, target deep)', () => {
    for (let i = 0; i < 500; i++) {
      parseCsrfTokenFromCookieString(MULTIPLE_COOKIES_DEEP)
    }
  })
})
