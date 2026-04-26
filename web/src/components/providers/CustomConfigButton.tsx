import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface CustomConfigButtonProps {
  onClick: () => void
}

/**
 * "Configure manually" entry point.
 *
 * Opens the provider credential form in custom-endpoint mode (no
 * preset preselection).  Intended for users with private gateways
 * or self-hosted Anthropic-compatible APIs not covered by a preset.
 */
export function CustomConfigButton({ onClick }: CustomConfigButtonProps) {
  return (
    <Button variant="outline" size="sm" onClick={onClick} className="gap-1.5">
      <Plus className="size-3.5" aria-hidden="true" />
      Configure manually
    </Button>
  )
}
