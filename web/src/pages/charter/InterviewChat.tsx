import { useState } from 'react'
import type { InterviewMessage } from '@/stores/charter'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SectionCard } from '@/components/ui/section-card'

export interface InterviewChatProps {
  messages: readonly InterviewMessage[]
  sending: boolean
  conversationClosed: boolean
  onSend: (message: string) => void
}

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

export function InterviewChat({
  messages,
  sending,
  conversationClosed,
  onSend,
}: InterviewChatProps) {
  const [draft, setDraft] = useState('')
  const canSend = draft.trim().length > 0 && !sending && !conversationClosed

  const handleSend = () => {
    if (!canSend) return
    onSend(draft.trim())
    setDraft('')
  }

  return (
    <SectionCard title="CEO interview">
      <div className="space-y-4">
        <div className="space-y-3" aria-live="polite">
          {messages.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Describe your product idea in a sentence. The CEO will interview
              you to build a project charter.
            </p>
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
        {conversationClosed && (
          <p className="text-sm text-muted-foreground">
            This interview is closed. Start a new one to draft another charter.
          </p>
        )}
      </div>
    </SectionCard>
  )
}
