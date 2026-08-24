import { Bell } from 'lucide-react'

import { EmptyState } from '@/components/ui/empty-state'
import type { NotificationFilterGroup } from '@/types/notifications'

const MESSAGES: Record<NotificationFilterGroup, string> = {
  all: 'No notifications yet',
  approvals: 'No approval notifications',
  budget: 'No budget notifications',
  system: 'No system notifications',
  tasks: 'No task notifications',
  workflows: 'No workflow notifications',
  agents: 'No agent notifications',
  providers: 'No provider notifications',
  connection: 'No connection notifications',
}

interface NotificationEmptyStateProps {
  readonly filter: NotificationFilterGroup
}

export function NotificationEmptyState({ filter }: NotificationEmptyStateProps) {
  return (
    <EmptyState
      icon={Bell}
      title={MESSAGES[filter]}
      description="You're all caught up."
    />
  )
}
