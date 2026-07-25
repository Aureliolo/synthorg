import { describe, it, expect } from 'vitest'
import { decodeModelRef, encodeModelRef, normalizeModelRef } from '@/utils/model-ref'

describe('decodeModelRef', () => {
  it('reads a canonical reference', () => {
    expect(decodeModelRef('{"provider":"p-1","model_id":"m-1"}')).toEqual({
      provider: 'p-1',
      modelId: 'm-1',
    })
  })

  it('reads the backend JSON spelling, which pads after : and ,', () => {
    expect(decodeModelRef('{"provider": "p-1", "model_id": "m-1"}')).toEqual({
      provider: 'p-1',
      modelId: 'm-1',
    })
  })

  it('treats an empty value as unset', () => {
    expect(decodeModelRef('')).toEqual({ provider: '', modelId: '' })
    expect(decodeModelRef('   ')).toEqual({ provider: '', modelId: '' })
  })

  it('reads a bare model id as model-only so a provider can be chosen', () => {
    // A setting persisted before provider binding holds a bare id; it must
    // survive as a model rather than being discarded.
    expect(decodeModelRef('m-1')).toEqual({ provider: '', modelId: 'm-1' })
  })

  it('strips padding from a bare model id', () => {
    // The picker preselects by string identity, so a padded id would match
    // no candidate and render as an empty select that still holds a value.
    expect(decodeModelRef('  m-1  ')).toEqual({ provider: '', modelId: 'm-1' })
  })

  it('falls back to model-only for malformed JSON', () => {
    expect(decodeModelRef('{not json')).toEqual({ provider: '', modelId: '{not json' })
  })

  it('reads JSON that is not an object as model-only', () => {
    // Only `{`-prefixed text is a candidate reference, so a JSON array never
    // reaches the destructuring path and cannot yield a half-populated pair.
    expect(decodeModelRef('["p-1","m-1"]')).toEqual({
      provider: '',
      modelId: '["p-1","m-1"]',
    })
  })

  it('ignores non-string members', () => {
    expect(decodeModelRef('{"provider":1,"model_id":null}')).toEqual({
      provider: '',
      modelId: '',
    })
  })
})

describe('encodeModelRef', () => {
  it('emits the canonical key set', () => {
    expect(JSON.parse(encodeModelRef('p-1', 'm-1'))).toEqual({
      provider: 'p-1',
      model_id: 'm-1',
    })
  })
})

describe('normalizeModelRef', () => {
  it('collapses both JSON spellings onto one string', () => {
    const padded = '{"provider": "p-1", "model_id": "m-1"}'
    const compact = '{"provider":"p-1","model_id":"m-1"}'
    expect(padded).not.toBe(compact)
    expect(normalizeModelRef(padded)).toBe(normalizeModelRef(compact))
  })

  it('leaves a bare model id untouched', () => {
    // Only a fully bound pair is rewritten; anything else round-trips so a
    // plain-string setting is never corrupted by normalisation.
    expect(normalizeModelRef('m-1')).toBe('m-1')
  })

  it('leaves an empty value untouched', () => {
    expect(normalizeModelRef('')).toBe('')
  })

  it('leaves a provider-less reference untouched', () => {
    const unbound = '{"provider":"","model_id":"m-1"}'
    expect(normalizeModelRef(unbound)).toBe(unbound)
  })
})
