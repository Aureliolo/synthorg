import { memo } from 'react'
import { Wrench } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { formatLabel } from '@/utils/format'
import { cn } from '@/lib/utils'

interface ToolBadgesProps {
  tools: readonly string[]
  className?: string
}

function ToolBadge({ tool }: { tool: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md px-2 py-0.5',
        'bg-accent/8 text-accent border border-accent/20',
        'font-mono text-compact',
      )}
    >
      {formatLabel(tool)}
    </span>
  )
}

function ToolBadgesImpl({ tools, className }: ToolBadgesProps) {
  if (tools.length === 0) return null

  return (
    <SectionCard title="Tools" icon={Wrench} className={className}>
      <div className="flex flex-wrap gap-2">
        {tools.map((tool, index) => (
          // eslint-disable-next-line @eslint-react/no-array-index-key -- tool names may repeat; index as tiebreaker
          <ToolBadge key={`${tool}-${index}`} tool={tool} />
        ))}
      </div>
    </SectionCard>
  )
}

export const ToolBadges = memo(ToolBadgesImpl)
