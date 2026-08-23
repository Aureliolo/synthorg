import { ROLE_BADGE_COLORS } from '@/styles/status-colors'

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

  it('uses no hex colour literals', () => {
    for (const value of Object.values(ROLE_BADGE_COLORS)) {
      expect(value).not.toMatch(/#[0-9a-fA-F]{3,8}/)
    }
  })
})
