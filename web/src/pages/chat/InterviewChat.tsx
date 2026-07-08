import { useState } from 'react'
import type { InterviewMessage } from '@/stores/charter'
import { Button } from '@/components/ui/button'
import { ExamplePrompts } from '@/components/ui/example-prompts'
import { InputField } from '@/components/ui/input-field'
import { SectionCard } from '@/components/ui/section-card'
import { useScrollToBottom } from './use-scroll-to-bottom'

const EXAMPLE_IDEAS: readonly string[] = [
  'A weekly customer-feedback digest summarised for the whole team.',
  'An internal tool that tracks competitor pricing changes.',
  'A self-serve onboarding guide for new API customers.',
]

export interface InterviewChatProps {
  messages: readonly InterviewMessage[]
  sending: boolean
  conversationClosed: boolean
  onSend: (message: string) => void
}

// Local chat-bubble for the charter interview. Not promoted to
// `components/ui/` because it has a single caller and a single shape;
// if a second caller appears it should move to a shared
// `ui/chat-bubble.tsx` with stories.
function ChatBubble({ message }: { message: InterviewMessage }) {
  const isUser = message.role === 'user'
  return (
    <div className={isUser ? 'flex justify-end' : 'flex justify-start'}>
      <div
        className={
          isUser
            ? 'max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground'
            : 'max-w-[80%] rounded-lg bg-muted px-3 py-2 text-sm text-foreground'
        }
      >
        {message.content}
      </div>
    </div>
  )
}

// Footer status line below the composer: a "still working" cue while a turn
// is in flight (model calls can run for a minute), then a closed-interview
// note. Extracted so the main component stays under the complexity cap.
function InterviewStatusHint({
  sending,
  conversationClosed,
}: {
  sending: boolean
  conversationClosed: boolean
}) {
  if (sending) {
    return (
      <p className="text-sm text-muted-foreground" role="status">
        The CEO is thinking. This can take up to a minute on slower providers.
      </p>
    )
  }
  if (conversationClosed) {
    return (
      <p className="text-sm text-muted-foreground">
        This interview is closed. Start a new one to draft another charter.
      </p>
    )
  }
  return null
}

export function InterviewChat({
  messages,
  sending,
  conversationClosed,
  onSend,
}: InterviewChatProps) {
  const [draft, setDraft] = useState('')
  const scrollRef = useScrollToBottom(messages)
  const canSend = draft.trim().length > 0 && !sending && !conversationClosed

  const handleSend = () => {
    if (!canSend) return
    onSend(draft.trim())
    setDraft('')
  }

  return (
    <SectionCard title="CEO interview">
      <div className="space-y-4">
        <div
          ref={scrollRef}
          role="log"
          aria-label="CEO interview transcript"
          className="max-h-80 space-y-3 overflow-y-auto"
        >
          {messages.length === 0 ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Describe your product idea in a sentence. The CEO will interview
                you to build a project charter.
              </p>
              <ExamplePrompts
                prompts={EXAMPLE_IDEAS}
                onSelect={setDraft}
                disabled={sending || conversationClosed}
              />
            </div>
          ) : (
            messages.map((message) => (
              <ChatBubble key={message.id} message={message} />
            ))
          )}
        </div>
        <InputField
          label="Your message"
          multiline
          rows={3}
          value={draft}
          onValueChange={setDraft}
          disabled={sending || conversationClosed}
        />
        <div className="flex justify-end">
          <Button onClick={handleSend} disabled={!canSend}>
            {sending ? 'Sending...' : 'Send'}
          </Button>
        </div>
        <InterviewStatusHint
          sending={sending}
          conversationClosed={conversationClosed}
        />
      </div>
    </SectionCard>
  )
}
