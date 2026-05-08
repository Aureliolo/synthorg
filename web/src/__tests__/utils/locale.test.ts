import fc from 'fast-check'
import { APP_LOCALE_FALLBACK, getLocale, resolveLocale } from '@/utils/locale'

describe('APP_LOCALE_FALLBACK', () => {
  it('is plain "en" (neutral language, no region)', () => {
    // The fallback deliberately carries no region. "en-US" (or any
    // other language-region) would privilege one locale's date,
    // number, and unit defaults when no browser tag is available.
    // Plain "en" lets Intl pick neutral defaults from the language
    // subtag alone.
    expect(APP_LOCALE_FALLBACK).toBe('en')
  })

  it('canonicalizes to itself via Intl.getCanonicalLocales', () => {
    expect(Intl.getCanonicalLocales(APP_LOCALE_FALLBACK)).toEqual(['en'])
  })

  it('is a valid Intl locale', () => {
    expect(() => new Intl.Locale(APP_LOCALE_FALLBACK)).not.toThrow()
  })

  it.each([
    'en_US', // underscore instead of hyphen
    '', // empty string
    'not a locale', // whitespace in subtag
  ])('Intl rejects the malformed candidate %j', (candidate) => {
    expect(() => Intl.getCanonicalLocales(candidate)).toThrow()
  })
})

describe('getLocale', () => {
  it('returns a string', () => {
    expect(typeof getLocale()).toBe('string')
  })

  it('returns a value usable by Intl APIs', () => {
    const locale = getLocale()
    expect(() =>
      new Intl.NumberFormat(locale).format(1000),
    ).not.toThrow()
    expect(() =>
      new Intl.DateTimeFormat(locale).format(new Date()),
    ).not.toThrow()
  })
})

describe('resolveLocale', () => {
  it('prefers a valid override over the browser locale', () => {
    expect(resolveLocale('de-CH', 'en-US')).toBe('de-CH')
  })

  it('trims whitespace around the override', () => {
    expect(resolveLocale('  fr-FR  ', 'en-US')).toBe('fr-FR')
  })

  it('falls through to the browser locale on blank override', () => {
    expect(resolveLocale('   ', 'fr-FR')).toBe('fr-FR')
  })

  it('falls through to the browser locale on malformed override', () => {
    // ``123!!!`` is syntactically invalid per BCP 47
    // (language subtag must be alpha); Intl.getCanonicalLocales
    // throws, so the override is discarded.
    expect(resolveLocale('123!!!', 'fr-FR')).toBe('fr-FR')
  })

  it('falls back to APP_LOCALE_FALLBACK on null inputs', () => {
    expect(resolveLocale(null, null)).toBe(APP_LOCALE_FALLBACK)
  })

  it('falls back to APP_LOCALE_FALLBACK when the browser reports a malformed locale', () => {
    expect(resolveLocale(null, 'not a locale')).toBe(APP_LOCALE_FALLBACK)
  })
})

describe('resolveLocale property tests', () => {
  // Generate plausibly-valid BCP 47 tags by joining a primary subtag
  // with an optional region, mirroring how the browser surfaces
  // ``navigator.language`` (``de``, ``de-CH``, ``en-US``, ...). The
  // synthesis is deliberately narrow -- pathological inputs are
  // covered by the malformed-input property below.
  const bcp47Tag = fc
    .tuple(
      fc.stringMatching(/^[a-z]{2,3}$/),
      fc.option(fc.stringMatching(/^[A-Z]{2}$/), { nil: undefined }),
    )
    .map(([primary, region]) => (region ? `${primary}-${region}` : primary))

  // Arbitrary user-supplied "override" string. fast-check's ``string``
  // generator includes whitespace, control bytes, and Unicode noise --
  // exactly the malformed-override surface the catch-and-skip
  // semantics need to handle without throwing.
  const arbitraryOverride = fc.oneof(
    fc.constant(null),
    fc.constant(undefined),
    fc.string(),
    bcp47Tag,
  )
  const arbitraryBrowser = fc.oneof(
    fc.constant(null),
    fc.constant(undefined),
    fc.string(),
    bcp47Tag,
  )

  it('never throws on any input combination', () => {
    fc.assert(
      fc.property(arbitraryOverride, arbitraryBrowser, (override, browser) => {
        expect(() => resolveLocale(override, browser)).not.toThrow()
      }),
    )
  })

  it('always returns a string usable by Intl APIs', () => {
    fc.assert(
      fc.property(arbitraryOverride, arbitraryBrowser, (override, browser) => {
        const result = resolveLocale(override, browser)
        expect(typeof result).toBe('string')
        expect(result.length).toBeGreaterThan(0)
        expect(() => new Intl.NumberFormat(result)).not.toThrow()
        expect(() => new Intl.DateTimeFormat(result)).not.toThrow()
      }),
    )
  })

  it('whitespace around the override is idempotent', () => {
    fc.assert(
      fc.property(
        bcp47Tag,
        fc.stringMatching(/^[ \t]{0,4}$/),
        fc.stringMatching(/^[ \t]{0,4}$/),
        bcp47Tag,
        (tag, leading, trailing, browser) => {
          const padded = `${leading}${tag}${trailing}`
          expect(resolveLocale(padded, browser)).toBe(
            resolveLocale(tag, browser),
          )
        },
      ),
    )
  })

  it('a malformed override falls through to a valid browser locale', () => {
    fc.assert(
      // Primary subtag must be alpha; ``123abc`` is a syntactically
      // invalid BCP 47 tag and ``Intl.getCanonicalLocales`` rejects it.
      fc.property(
        fc.stringMatching(/^[0-9!@#]{3,8}$/),
        bcp47Tag,
        (badOverride, validBrowser) => {
          expect(resolveLocale(badOverride, validBrowser)).toBe(validBrowser)
        },
      ),
    )
  })
})
