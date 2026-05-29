import { useCallback, useState } from 'react'
import type { CharterEditRequest } from '@/api/types'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ListHeader } from '@/components/ui/list-header'
import { SectionCard } from '@/components/ui/section-card'
import { useCharterStore } from '@/stores/charter'
import { CharterDraftCard } from './charter/CharterDraftCard'
import { InterviewChat } from './charter/InterviewChat'

function useCharterInterview() {
  const messages = useCharterStore((s) => s.messages)
  const sending = useCharterStore((s) => s.sending)
  const conversationClosed = useCharterStore((s) => s.conversationClosed)
  const draftCharter = useCharterStore((s) => s.draftCharter)
  const runTurn = useCharterStore((s) => s.runTurn)
  const editDraft = useCharterStore((s) => s.editDraft)
  const approve = useCharterStore((s) => s.approve)
  const cancel = useCharterStore((s) => s.cancel)
  const resetInterview = useCharterStore((s) => s.resetInterview)

  const [mutating, setMutating] = useState(false)

  const handleSend = useCallback(
    (message: string) => {
      void runTurn(message)
    },
    [runTurn],
  )

  const handleSave = useCallback(
    (data: CharterEditRequest) => {
      if (!draftCharter) return
      setMutating(true)
      void editDraft(draftCharter.id, data).finally(() => {
        setMutating(false)
      })
    },
    [draftCharter, editDraft],
  )

  const handleApprove = useCallback(() => {
    if (!draftCharter) return
    setMutating(true)
    void approve(draftCharter.id).finally(() => {
      setMutating(false)
    })
  }, [draftCharter, approve])

  const handleCancel = useCallback(() => {
    if (!draftCharter) return
    setMutating(true)
    void cancel(draftCharter.id).finally(() => {
      setMutating(false)
    })
  }, [draftCharter, cancel])

  return {
    messages, sending, conversationClosed, draftCharter, resetInterview,
    mutating, handleSend, handleSave, handleApprove, handleCancel,
  }
}

export default function CharterInterviewPage() {
  const {
    messages,
    sending,
    conversationClosed,
    draftCharter,
    resetInterview,
    mutating,
    handleSend,
    handleSave,
    handleApprove,
    handleCancel,
  } = useCharterInterview()

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Project charters"
        description="Interview the CEO to turn an idea into an approvable project charter."
        primaryAction={
          <Button
            variant="outline"
            onClick={resetInterview}
            disabled={sending || mutating}
          >
            New interview
          </Button>
        }
      />
      <div className="grid gap-card-gap lg:grid-cols-2">
        <InterviewChat
          messages={messages}
          sending={sending}
          conversationClosed={conversationClosed}
          onSend={handleSend}
        />
        {draftCharter ? (
          // ``key`` forces a fresh mount when the parent supplies a
          // new charter or bumps its version so the card's local
          // brief / amount state initialises from the refreshed prop
          // instead of carrying stale edits across the swap.
          <CharterDraftCard
            key={`${draftCharter.id}:${draftCharter.version}`}
            charter={draftCharter}
            busy={mutating || sending}
            onSave={handleSave}
            onApprove={handleApprove}
            onCancel={handleCancel}
          />
        ) : (
          <SectionCard title="Charter draft">
            <EmptyState
              title="No charter yet"
              description="Answer the CEO's questions. Once the requirements are clear, a charter draft appears here for review and approval."
            />
          </SectionCard>
        )}
      </div>
    </div>
  )
}
