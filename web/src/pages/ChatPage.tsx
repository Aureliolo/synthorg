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
import type { ChiefOfStaffFlags } from '@/api/endpoints/meta'
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
  /**
   * Config flag gating this mode, or ``null`` for always-available
   * modes. The backend live-gates every request anyway, so an absent
   * config fails OPEN: better to let the server answer with its
   * specific 503 than to hide a working feature.
   */
  readonly flag: keyof ChiefOfStaffFlags | null
  /** Settings key shown when the mode is switched off. */
  readonly settingKey: string | null
  /**
   * The per-capability model field on the config, or ``null`` when the
   * mode uses the acting agent's own model (group, direct action). When
   * the mode is enabled but this field is blank, the server 503s, so the
   * dashboard surfaces the missing model setting inline. Fails OPEN when
   * the config is unknown, exactly like the flag gate.
   */
  readonly modelField:
    | 'chat_model'
    | 'propose_model'
    | 'routing_model'
    | 'narrative_model'
    | null
  /**
   * When true, the mode also needs security governance wired
   * (``direct_mcp_ready``). Its toggle can be on yet the path stays
   * fail-closed until the MCP self-consumer + a SecurityConfig are set,
   * so the dashboard cross-warns instead of silently 503-ing.
   */
  readonly requiresGovernance: boolean
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
    flag: 'chat_enabled',
    settingKey: 'chief_of_staff.explain_chat_enabled',
    modelField: 'chat_model',
    requiresGovernance: false,
  },
  {
    value: 'work',
    label: 'Request work',
    icon: ClipboardList,
    title: 'Request work',
    explainer:
      'Describe work in natural language. The Chief of Staff clarifies, then queues concrete work items for your approval before anything runs.',
    component: () => <RequestWorkChat />,
    flag: 'propose_enabled',
    settingKey: 'chief_of_staff.propose_enabled',
    modelField: 'propose_model',
    requiresGovernance: false,
  },
  {
    value: 'group',
    label: 'Group chat',
    icon: Users,
    title: 'Group chat',
    explainer:
      'Talk with several agents in one conversation. Every participant sees the shared transcript and responds each round.',
    component: () => <GroupChat />,
    flag: 'group_chat_enabled',
    settingKey: 'chief_of_staff.group_chat_enabled',
    modelField: null,
    requiresGovernance: false,
  },
  {
    value: 'action',
    label: 'Direct action',
    icon: Zap,
    title: 'Direct action',
    explainer:
      'Instruct one agent to act with its own tools. Sensitive actions park in the approval queue instead of running immediately.',
    component: () => <DirectActionChat />,
    flag: 'direct_mcp_enabled',
    settingKey: 'chief_of_staff.direct_mcp_enabled',
    modelField: null,
    requiresGovernance: true,
  },
  {
    value: 'project',
    label: 'New project',
    icon: Rocket,
    title: 'New project',
    explainer:
      'Pitch an idea to the CEO. It interviews you, drafts a project charter beside the conversation, and the approved charter becomes a project.',
    component: () => <ProjectInterview />,
    flag: null,
    settingKey: null,
    modelField: null,
    requiresGovernance: false,
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

function DisabledModeNotice({ settingKey }: { settingKey: string }) {
  return (
    <EmptyState
      icon={MessagesSquare}
      title="This conversation mode is switched off"
      description={`Enable the ${settingKey} setting to use it. The other modes stay available.`}
    />
  )
}

function MissingModelNotice({ settingKey }: { settingKey: string }) {
  return (
    <EmptyState
      icon={MessagesSquare}
      title="No model is configured for this mode"
      description={`Set the ${settingKey} setting to a model to use it. The other modes stay available.`}
    />
  )
}

function MissingGovernanceNotice() {
  return (
    <EmptyState
      icon={MessagesSquare}
      title="This mode is enabled but not yet live"
      description="Direct action stays fail-closed until security governance is configured: set the security.mcp_self_consumer mode and a SecurityConfig. The toggle takes effect with no restart once they are set."
    />
  )
}

type ModeGate =
  | { kind: 'flag-off'; settingKey: string }
  | { kind: 'model-missing'; settingKey: string }
  | { kind: 'governance-missing' }

function modelMissingGate(
  mode: ModeDefinition,
  flags: ChiefOfStaffFlags,
): ModeGate | null {
  return mode.modelField !== null && !flags[mode.modelField]
    ? { kind: 'model-missing', settingKey: `chief_of_staff.${mode.modelField}` }
    : null
}

function governanceGate(
  mode: ModeDefinition,
  flags: ChiefOfStaffFlags,
): ModeGate | null {
  return mode.requiresGovernance && !flags.direct_mcp_ready
    ? { kind: 'governance-missing' }
    : null
}

// Fail open when the config is unknown: the endpoints live-gate every request
// server-side, and a specific 503 beats wrongly hiding a working feature
// behind stale client state. A switched-off mode short-circuits before the
// model / governance checks, which would otherwise read stale sub-config.
function modeGate(
  mode: ModeDefinition,
  flags: ChiefOfStaffFlags | undefined,
): ModeGate | null {
  if (flags === undefined) return null
  if (mode.flag !== null && !flags[mode.flag]) {
    return mode.settingKey !== null
      ? { kind: 'flag-off', settingKey: mode.settingKey }
      : null
  }
  return modelMissingGate(mode, flags) ?? governanceGate(mode, flags)
}

function ModeGateNotice({ gate }: { gate: ModeGate }) {
  switch (gate.kind) {
    case 'flag-off':
      return <DisabledModeNotice settingKey={gate.settingKey} />
    case 'model-missing':
      return <MissingModelNotice settingKey={gate.settingKey} />
    case 'governance-missing':
      return <MissingGovernanceNotice />
  }
}

function ModePanel({ mode, flags }: {
  mode: ModeDefinition
  flags: ChiefOfStaffFlags | undefined
}) {
  const gate = modeGate(mode, flags)
  return (
    <SectionCard title={mode.title} icon={mode.icon}>
      <div className="flex flex-col gap-section-gap">
        <p className="text-xs text-text-secondary">{mode.explainer}</p>
        {gate === null ? mode.component() : <ModeGateNotice gate={gate} />}
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
      {config === null && active.flag !== null ? (
        <SkeletonCard className="h-64" />
      ) : (
        <ModePanel mode={active} flags={config?.chief_of_staff} />
      )}
    </div>
  )
}
