import {
  ESCALATION_STATUS_BADGE_COLORS,
  ROLE_BADGE_COLORS,
} from '@/styles/status-colors'

// The TypeScript compile-time `Record<EnumValue, string>` constraint
// is the primary drift guard for these maps; this file pins a
// runtime check so an IDE that suppresses TS errors still surfaces
// drift via a failing test.

describe('status-colors', () => {
  it('ROLE_BADGE_COLORS covers every OrgRole', () => {
    const expected = ['owner', 'department_admin', 'editor', 'viewer'] as const
    expect(Object.keys(ROLE_BADGE_COLORS).sort()).toEqual([...expected].sort())
    for (const role of expected) {
      expect(ROLE_BADGE_COLORS[role]).toMatch(/text-|bg-/)
    }
  })

  it('ESCALATION_STATUS_BADGE_COLORS covers every EscalationStatus', () => {
    const expected = ['pending', 'decided', 'expired', 'cancelled'] as const
    expect(Object.keys(ESCALATION_STATUS_BADGE_COLORS).sort()).toEqual(
      [...expected].sort(),
    )
    for (const status of expected) {
      expect(ESCALATION_STATUS_BADGE_COLORS[status]).toMatch(/text-|bg-/)
    }
  })

  it('uses no hex colour literals', () => {
    const allValues = [
      ...Object.values(ROLE_BADGE_COLORS),
      ...Object.values(ESCALATION_STATUS_BADGE_COLORS),
    ]
    for (const value of allValues) {
      expect(value).not.toMatch(/#[0-9a-fA-F]{3,8}/)
    }
  })
})
