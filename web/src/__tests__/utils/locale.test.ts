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
