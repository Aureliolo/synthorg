import { SegmentedControl } from '@/components/ui/segmented-control'
import type { NotificationFilterGroup } from '@/types/notifications'
import { CATEGORY_CONFIGS, FILTER_GROUP_LABELS } from '@/types/notifications'

const GROUPS: readonly NotificationFilterGroup[] = [
  'all',
  ...new Set(Object.values(CATEGORY_CONFIGS).map((c) => c.group)),
]

interface NotificationFilterBarProps {
  readonly value: NotificationFilterGroup
  readonly onChange: (group: NotificationFilterGroup) => void
}

export function NotificationFilterBar({ value, onChange }: NotificationFilterBarProps) {
  return (
    <SegmentedControl<NotificationFilterGroup>
      label="Notification filter"
      value={value}
      onChange={onChange}
      options={GROUPS.map((g) => ({ value: g, label: FILTER_GROUP_LABELS[g] }))}
      size="sm"
    />
  )
}
