import { describe, expect, it } from 'vitest'
import { POSTURE_INFO } from '@/utils/posture-info'
import { POSTURE_NAME_VALUES } from '@/api/types/enum-values.gen'

describe('POSTURE_INFO', () => {
  it('has an entry for every generated posture name', () => {
    for (const name of POSTURE_NAME_VALUES) {
      expect(POSTURE_INFO[name]).toBeDefined()
    }
    expect(Object.keys(POSTURE_INFO).sort()).toEqual([...POSTURE_NAME_VALUES].sort())
  })

  it('every entry has a label, description, flags, and tone', () => {
    for (const info of Object.values(POSTURE_INFO)) {
      expect(info.label.length).toBeGreaterThan(0)
      expect(info.description.length).toBeGreaterThan(0)
      expect(info.featureFlags.length).toBeGreaterThan(0)
      expect(['accent', 'success', 'warning', 'danger', 'muted']).toContain(info.tone)
    }
  })
})
