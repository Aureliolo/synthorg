import { createElement } from 'react'
import { TrendingDown, TrendingUp, type LucideProps } from 'lucide-react'
import type { PromotionDirection } from '@/api/types/enum-values.gen'

export interface PromotionDirectionIconProps extends LucideProps {
  direction: PromotionDirection
}

/**
 * Render the trend icon for a promotion direction (up for ``promotion``,
 * down otherwise).
 *
 * The lookup happens inside this component (not at the call site) so the
 * ``react-x/static-components`` rule sees a stable, top-level component
 * declaration at every JSX usage. Lives in its own file so React Fast
 * Refresh stays happy: the ``react-refresh/only-export-components`` rule
 * requires component-only modules.
 */
export function PromotionDirectionIcon({ direction, ...rest }: PromotionDirectionIconProps) {
  return createElement(direction === 'promotion' ? TrendingUp : TrendingDown, rest)
}
