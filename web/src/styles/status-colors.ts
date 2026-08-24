/**
 * Shared badge / pill colour lookups for cross-page status enums.
 *
 * Each map is `Record<EnumValue, string>` keyed by the typed enum
 * union so adding a new enum member without a colour entry is a TS
 * error. Class strings use the existing semantic Tailwind tokens
 * declared in `web/src/styles/design-tokens.css` (bg-warning,
 * text-warning, border-warning/20 etc.) -- no hex literals.
 *
 * Approval-domain colours have their own helper layer in
 * `@/utils/approvals` (`getRiskLevelColor`, `getUrgencyColor`,
 * `URGENCY_BADGE_CLASSES`) that maps via the `SemanticColor` token
 * abstraction; do NOT duplicate them here.
 */

import type { OrgRole } from '@/api/types/enums'

/** Pill classes for org-role badges (owner / department_admin / editor / viewer). */
export const ROLE_BADGE_COLORS: Record<OrgRole, string> = {
  owner: 'bg-accent/10 text-accent border-accent/20',
  department_admin: 'bg-warning/10 text-warning border-warning/20',
  editor: 'bg-info/10 text-info border-info/20',
  viewer: 'bg-surface text-text-secondary border-border',
}
