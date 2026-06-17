/**
 * Supported currencies for the setup wizard.
 *
 * Mirrors the backend's `CURRENCY_SYMBOLS` table in
 * `src/synthorg/budget/currency.py`. If the backend adds an endpoint
 * for available currencies, this should be replaced with an API call.
 */

/** ISO 4217 currency codes with display labels, ordered by usage frequency. */
export const CURRENCY_OPTIONS = [
  { value: 'EUR', label: 'EUR - Euro' },
  { value: 'USD', label: 'USD - US Dollar' },
  { value: 'GBP', label: 'GBP - British Pound' },
  { value: 'JPY', label: 'JPY - Japanese Yen' },
  { value: 'CHF', label: 'CHF - Swiss Franc' },
  { value: 'CAD', label: 'CAD - Canadian Dollar' },
  { value: 'AUD', label: 'AUD - Australian Dollar' },
  { value: 'CNY', label: 'CNY - Chinese Yuan' },
  { value: 'INR', label: 'INR - Indian Rupee' },
  { value: 'KRW', label: 'KRW - South Korean Won' },
  { value: 'BRL', label: 'BRL - Brazilian Real' },
  { value: 'MXN', label: 'MXN - Mexican Peso' },
  { value: 'SGD', label: 'SGD - Singapore Dollar' },
  { value: 'HKD', label: 'HKD - Hong Kong Dollar' },
  { value: 'NZD', label: 'NZD - New Zealand Dollar' },
  { value: 'SEK', label: 'SEK - Swedish Krona' },
  { value: 'NOK', label: 'NOK - Norwegian Krone' },
  { value: 'DKK', label: 'DKK - Danish Krone' },
  { value: 'PLN', label: 'PLN - Polish Zloty' },
  { value: 'CZK', label: 'CZK - Czech Koruna' },
  { value: 'HUF', label: 'HUF - Hungarian Forint' },
  { value: 'TRY', label: 'TRY - Turkish Lira' },
  { value: 'ZAR', label: 'ZAR - South African Rand' },
  { value: 'THB', label: 'THB - Thai Baht' },
  { value: 'TWD', label: 'TWD - Taiwan Dollar' },
  { value: 'ILS', label: 'ILS - Israeli Shekel' },
  { value: 'IDR', label: 'IDR - Indonesian Rupiah' },
  { value: 'VND', label: 'VND - Vietnamese Dong' },
] as const satisfies readonly { value: string; label: string }[]

/** Derived union type of supported ISO 4217 currency codes. */
export type CurrencyCode = (typeof CURRENCY_OPTIONS)[number]['value']

/** Default currency code (matches backend DEFAULT_CURRENCY).
 *
 * USD is the default because every LLM provider SynthOrg integrates with
 * prices tokens in USD. Changing the runtime setting relabels the display
 * symbol for future rows but does not convert numeric values -- SynthOrg
 * performs no FX conversion. */
export const DEFAULT_CURRENCY: CurrencyCode = 'USD'

const CURRENCY_CODES: ReadonlySet<string> = new Set(
  CURRENCY_OPTIONS.map((option) => option.value),
)

/** Narrow an arbitrary string to a known {@link CurrencyCode}. */
export function isCurrencyCode(value: string): value is CurrencyCode {
  return CURRENCY_CODES.has(value)
}
