import { normalisedKey } from '@/utils/keyboard'

describe('normalisedKey', () => {
  it('lower-cases the key so a shortcut survives Caps Lock', () => {
    expect(normalisedKey({ key: 'K' })).toBe('k')
    expect(normalisedKey({ key: 'ArrowLeft' })).toBe('arrowleft')
  })

  it('returns an empty string for an event carrying no key', () => {
    expect(normalisedKey({})).toBe('')
  })

  it('accepts a real KeyboardEvent', () => {
    expect(normalisedKey(new KeyboardEvent('keydown', { key: 'S' }))).toBe('s')
  })
})
