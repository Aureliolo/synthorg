import { Link } from 'react-router'
import { Sparkles } from 'lucide-react'
import { cn, FOCUS_RING } from '@/lib/utils'
import { useRecommendationsStore } from '@/stores/recommendations'

/**
 * Link to the model-recommendations review page, badged with the count
 * of pending recommendations. Pure display: the count is fetched by the
 * hosting page. Renders nothing while the count is zero so it stays out
 * of the way until the refresh service surfaces an upgrade.
 */
export function RecommendationsLink() {
  const count = useRecommendationsStore((s) => s.recommendations.length)

  if (count === 0) return null
  return (
    <Link
      to="/agents/model-recommendations"
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md border border-accent/30 bg-accent/10',
        'px-2.5 py-1 text-compact font-medium text-accent transition-colors hover:bg-accent/20',
        FOCUS_RING,
      )}
    >
      <Sparkles className="size-3.5" aria-hidden="true" />
      {count} upgrade{count === 1 ? '' : 's'} available
    </Link>
  )
}
