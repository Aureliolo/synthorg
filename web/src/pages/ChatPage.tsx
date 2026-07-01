import { useEffect } from 'react'
import { useSearchParams } from 'react-router'
import {
  ClipboardList,
  MessageCircle,
  MessagesSquare,
  Rocket,
  Users,
  Zap,
  type LucideIcon,
} from 'lucide-react'

import { EmptyState } from '@/components/ui/empty-state'
import { SectionCard } from '@/components/ui/section-card'
import {
  SegmentedControl,
  type SegmentedControlOption,
} from '@/components/ui/segmented-control'
import { SkeletonCard } from '@/components/ui/skeleton'
import { useMetaStore } from '@/stores/meta'

import { ChiefOfStaffChat } from './chat/ChiefOfStaffChat'
import { DirectActionChat } from './chat/DirectActionChat'
import { GroupChat } from './chat/GroupChat'
import { ProjectInterview } from './chat/ProjectInterview'
import { RequestWorkChat } from './chat/RequestWorkChat'

type ChatMode = 'staff' | 'work' | 'group' | 'action' | 'project'

interface ModeDefinition {
  readonly value: ChatMode
  readonly label: string
  readonly icon: LucideIcon
  readonly title: string
  /** One-line answer to "who am I talking to and what happens". */
  readonly explainer: string
  readonly component: () => React.ReactNode
  /** Whether the mode needs the Chief of Staff feature to be enabled. */
  readonly needsChiefOfStaff: boolean
}

const MODES: readonly ModeDefinition[] = [
  {
    value: 'staff',
    label: 'Chief of Staff',
    icon: MessageCircle,
    title: 'Chief of Staff',
    explainer:
      'Ask the Chief of Staff about anything in the organisation: signals, spend, proposals, agent performance. Read-only: nothing is changed.',
    component: () => <ChiefOfStaffChat />,
    needsChiefOfStaff: true,
  },
  {
    value: 'work',
    label: 'Request work',
    icon: ClipboardList,
    title: 'Request work',
    explainer:
      'Describe work in natural language. The Chief of Staff clarifies, then queues concrete work items for your approval before anything runs.',
    component: () => <RequestWorkChat />,
    needsChiefOfStaff: true,
  },
  {
    value: 'group',
    label: 'Group chat',
    icon: Users,
    title: 'Group chat',
    explainer:
      'Talk with several agents in one conversation. Every participant sees the shared transcript and responds each round.',
    component: () => <GroupChat />,
    needsChiefOfStaff: true,
  },
  {
    value: 'action',
    label: 'Direct action',
    icon: Zap,
    title: 'Direct action',
    explainer:
      'Instruct one agent to act with its own tools. Sensitive actions park in the approval queue instead of running immediately.',
    component: () => <DirectActionChat />,
    needsChiefOfStaff: true,
  },
  {
    value: 'project',
    label: 'New project',
    icon: Rocket,
    title: 'New project',
    explainer:
      'Pitch an idea to the CEO. It interviews you, drafts a project charter beside the conversation, and the approved charter becomes a project.',
    component: () => <ProjectInterview />,
    needsChiefOfStaff: false,
  },
]

const MODE_OPTIONS: readonly SegmentedControlOption<ChatMode>[] = MODES.map(
  (m) => ({ value: m.value, label: m.label }),
)

function parseMode(raw: string | null): ChatMode {
  return MODES.some((m) => m.value === raw) ? (raw as ChatMode) : 'staff'
}

function ChatPageHeader() {
  return (
    <header>
      <h1 className="text-2xl font-semibold text-foreground">Chat</h1>
      <p className="text-sm text-muted-foreground">
        Talk to your organisation: ask, request work, discuss, direct, or start
        a project.
      </p>
    </header>
  )
}

function DisabledModeNotice() {
  return (
    <EmptyState
      icon={MessagesSquare}
      title="Conversational interface disabled"
      description="This mode is part of the Chief of Staff feature, which is switched off. Enable it under Settings > Chief of Staff to talk to your organisation here."
    />
  )
}

function ModePanel({ mode, chiefOfStaffEnabled }: {
  mode: ModeDefinition
  chiefOfStaffEnabled: boolean
}) {
  const blocked = mode.needsChiefOfStaff && !chiefOfStaffEnabled
  return (
    <SectionCard title={mode.title} icon={mode.icon}>
      <div className="flex flex-col gap-section-gap">
        <p className="text-xs text-text-secondary">{mode.explainer}</p>
        {blocked ? <DisabledModeNotice /> : mode.component()}
      </div>
    </SectionCard>
  )
}

export default function ChatPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const mode = parseMode(searchParams.get('mode'))
  const config = useMetaStore((s) => s.config)

  useEffect(() => {
    if (useMetaStore.getState().config === null) {
      void useMetaStore.getState().fetchConfig()
    }
  }, [])

  const active = MODES.find((m) => m.value === mode) ?? MODES[0]!
  // Gate on the Chief of Staff flag alone: the conversational endpoints
  // are live-gated server-side per request, so a mode the backend still
  // serves must not be blocked client-side by unrelated meta-loop state.
  const chiefOfStaffEnabled = Boolean(config?.chief_of_staff_enabled)

  return (
    <div className="space-y-section-gap">
      <ChatPageHeader />
      <SegmentedControl
        label="Conversation mode"
        options={MODE_OPTIONS}
        value={mode}
        onChange={(next) => {
          setSearchParams((prev) => {
            const params = new URLSearchParams(prev)
            params.set('mode', next)
            return params
          })
        }}
      />
      {config === null && active.needsChiefOfStaff ? (
        <SkeletonCard className="h-64" />
      ) : (
        <ModePanel mode={active} chiefOfStaffEnabled={chiefOfStaffEnabled} />
      )}
    </div>
  )
}
