import { Check, Copy } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/utils/format'

import { ChatMarkdown } from './chat-markdown'
import { ResponderAttribution } from './responder-attribution'

export type ChatBubbleVariant =
  | 'human'
  | 'assistant'
  | 'agent'
  | 'event'
  | 'notice'

export interface ChatBubbleProps {
  variant: ChatBubbleVariant
  /** Markdown (assistant/agent) or plain text (human) body. */
  content?: string | undefined
  /** Custom body for the event / notice variants. */
  children?: ReactNode
  /** ISO timestamp shown in the header; omitted when absent. */
  timestamp?: string | null | undefined
  /** Header role label (e.g. "You", "Chief of Staff"); ignored for agent. */
  roleLabel?: string | undefined
  /** Responding agent's display name (agent variant). */
  agentName?: string | undefined
  /** Responding agent's role (agent variant). */
  agentRole?: string | undefined
  /** Concern topic that routed to the agent (agent variant). */
  agentTopic?: string | null | undefined
  /** Danger tone for the notice variant (e.g. a failed turn). */
  isError?: boolean | undefined
  /**
   * The assistant answer is still streaming in: shows a caret and, crucially,
   * hides the bubble from assistive tech so a screen reader is not spammed
   * token-by-token (the transcript's own live region announces the state).
   */
  isStreaming?: boolean | undefined
  className?: string | undefined
}

const VARIANT_STYLES: Record<ChatBubbleVariant, string> = {
  human: 'ml-8 bg-accent/10',
  assistant: 'mr-8 bg-card',
  agent: 'mr-8 bg-card',
  event: 'mr-8 bg-card-hover',
  notice: 'mr-8 bg-card',
}

const DEFAULT_ROLE_LABEL: Partial<Record<ChatBubbleVariant, string>> = {
  human: 'You',
  assistant: 'Chief of Staff',
}

const MARKDOWN_VARIANTS: ReadonlySet<ChatBubbleVariant> = new Set([
  'assistant',
  'agent',
])

const COPIED_RESET_MS = 1500

/** Copy the bubble's text to the clipboard, with a brief confirmed state. */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current)
    },
    [],
  )

  const onCopy = useCallback(() => {
    const run = async () => {
      try {
        await navigator.clipboard.writeText(text)
        setCopied(true)
        if (timerRef.current !== null) clearTimeout(timerRef.current)
        timerRef.current = setTimeout(() => {
          setCopied(false)
        }, COPIED_RESET_MS)
      } catch {
        // Clipboard unavailable (insecure context / denied): a failed copy is
        // a no-op rather than an error the operator must act on.
      }
    }
    void run()
  }, [text])

  return (
    <button
      type="button"
      onClick={onCopy}
      className="rounded p-0.5 text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-accent focus-visible:outline-none"
      aria-label={copied ? 'Copied' : 'Copy message'}
    >
      {copied ? (
        <Check className="size-3.5 text-success" aria-hidden="true" />
      ) : (
        <Copy className="size-3.5" aria-hidden="true" />
      )}
    </button>
  )
}

function TimeStamp({ iso }: { iso: string }) {
  return (
    <time dateTime={iso} className="text-muted-foreground">
      {formatRelativeTime(iso)}
    </time>
  )
}

/** Header for an attributed specialist voice: avatar + name + role. */
function AgentHeader({
  name,
  role,
  topic,
  timestamp,
}: {
  name: string
  role?: string | undefined
  topic?: string | null | undefined
  timestamp?: string | null | undefined
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <ResponderAttribution name={name} role={role} topic={topic} className="mt-0" />
      {timestamp ? <TimeStamp iso={timestamp} /> : null}
    </div>
  )
}

/** Header for a labelled turn: a textual role label (never colour-only) + time. */
function LabelHeader({
  label,
  timestamp,
}: {
  label?: string | undefined
  timestamp?: string | null | undefined
}) {
  if (!label && !timestamp) return null
  return (
    <div className="flex items-center gap-2 text-xs text-text-secondary">
      {label ? <span className="font-medium text-foreground">{label}</span> : null}
      {timestamp ? <TimeStamp iso={timestamp} /> : null}
    </div>
  )
}

function BubbleHeader(props: ChatBubbleProps) {
  const { variant, agentName, agentRole } = props
  // Gate on the name alone so an agent bubble with no role (e.g. a
  // direct-action turn) still shows its attribution; the optional role passes
  // through and ResponderAttribution renders name-only when it is absent.
  if (variant === 'agent' && agentName) {
    return (
      <AgentHeader
        name={agentName}
        role={agentRole}
        topic={props.agentTopic}
        timestamp={props.timestamp}
      />
    )
  }
  return (
    <LabelHeader
      label={props.roleLabel ?? DEFAULT_ROLE_LABEL[variant]}
      timestamp={props.timestamp}
    />
  )
}

function BubbleBody({
  variant,
  content,
  children,
  isStreaming = false,
}: Pick<ChatBubbleProps, 'variant' | 'content' | 'children' | 'isStreaming'>) {
  const rendersMarkdown = MARKDOWN_VARIANTS.has(variant)
  const body =
    content === undefined ? null : rendersMarkdown ? (
      <ChatMarkdown content={content} />
    ) : (
      <p className="whitespace-pre-wrap text-foreground">{content}</p>
    )
  return (
    <>
      {body}
      {isStreaming && (
        <span
          className="inline-block h-4 w-1.5 animate-pulse bg-foreground/60 align-text-bottom"
          aria-hidden
        />
      )}
      {children}
      {/* No copy affordance mid-stream: the text is still growing. */}
      {rendersMarkdown && content && !isStreaming ? (
        <div className="flex justify-end">
          <CopyButton text={content} />
        </div>
      ) : null}
    </>
  )
}

/**
 * The one chat bubble for every conversational surface. Role is conveyed by a
 * textual label (or agent attribution), never colour alone; assistant/agent
 * bodies render as sanitised markdown with a copy affordance; event and notice
 * bubbles carry arbitrary inline content.
 */
export function ChatBubble(props: ChatBubbleProps) {
  const {
    variant,
    content,
    children,
    isError = false,
    isStreaming = false,
    className,
  } = props
  return (
    <div
      // Hidden from assistive tech while streaming so a screen reader is not
      // read every token; the finalised bubble (isStreaming false) is announced.
      aria-hidden={isStreaming || undefined}
      className={cn(
        'space-y-1.5 rounded-md p-card text-sm',
        VARIANT_STYLES[variant],
        isError && 'border border-danger/30 bg-danger/10 text-danger',
        className,
      )}
    >
      <BubbleHeader {...props} />
      <BubbleBody variant={variant} content={content} isStreaming={isStreaming}>
        {children}
      </BubbleBody>
    </div>
  )
}
