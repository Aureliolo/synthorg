import { SelectField } from '@/components/ui/select-field'
import { formatLabel } from '@/utils/format'
import { getMeetingStatusLabel, type MeetingPageFilters } from '@/utils/meetings'
import { MEETING_STATUS_VALUES } from '@/api/types/meetings'
import { makeEnumParser } from '@/utils/type-guards'

const parseMeetingStatus = makeEnumParser(MEETING_STATUS_VALUES)

interface MeetingFilterBarProps {
  filters: MeetingPageFilters
  onFiltersChange: (filters: MeetingPageFilters) => void
  meetingTypes: readonly string[]
  className?: string
}

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  ...MEETING_STATUS_VALUES.map((s) => ({ value: s, label: getMeetingStatusLabel(s) })),
]

export function MeetingFilterBar({
  filters,
  onFiltersChange,
  meetingTypes,
  className,
}: MeetingFilterBarProps) {
  const typeOptions = [
    { value: '', label: 'All types' },
    ...meetingTypes.map((t) => ({ value: t, label: formatLabel(t) })),
  ]

  return (
    <div className={className}>
      <div className="flex items-center gap-3">
        <SelectField
          label="Status"
          value={filters.status ?? ''}
          onChange={(val) =>
            onFiltersChange({ ...filters, status: parseMeetingStatus(val) })
          }
          options={STATUS_OPTIONS}
          className="w-44"
        />
        <SelectField
          label="Type"
          value={filters.meetingType ?? ''}
          onChange={(val) =>
            onFiltersChange({
              ...filters,
              meetingType: val || undefined,
            })
          }
          options={typeOptions}
          className="w-44"
        />
      </div>
    </div>
  )
}
