import { useCallback } from 'react'

import type { CharterEditRequest } from '@/api/types/charter'
import { EmptyState } from '@/components/ui/empty-state'
import { SectionCard } from '@/components/ui/section-card'
import { useCharterStore } from '@/stores/charter'

import { CharterDraftCard } from './CharterDraftCard'

/**
 * The live charter draft beside the unified conversation.
 *
 * When a turn resolves to the charter capability, the draft is hydrated into
 * the charter store ({@link useCharterStore.hydrateFromTurn}); this panel
 * renders it and routes edit / approve / cancel through the charter store's
 * own mutations, so the drafting flow is unchanged: only its entry point
 * moved from a separate mode to one conversation.
 */
export function CharterSidePanel() {
  const draftCharter = useCharterStore((s) => s.draftCharter)
  const mutating = useCharterStore((s) => s.mutating)
  const editDraft = useCharterStore((s) => s.editDraft)
  const approve = useCharterStore((s) => s.approve)
  const cancel = useCharterStore((s) => s.cancel)

  const handleSave = useCallback(
    (data: CharterEditRequest) => {
      if (draftCharter) void editDraft(draftCharter.id, data)
    },
    [draftCharter, editDraft],
  )
  const handleApprove = useCallback(() => {
    if (draftCharter) void approve(draftCharter.id)
  }, [draftCharter, approve])
  const handleCancel = useCallback(() => {
    if (draftCharter) void cancel(draftCharter.id)
  }, [draftCharter, cancel])

  if (!draftCharter) {
    return (
      <SectionCard title="Charter draft">
        <EmptyState
          title="No charter yet"
          description="Describe a product idea in the conversation. The CEO interviews you, and once the requirements are clear a charter draft appears here to review and approve."
        />
      </SectionCard>
    )
  }
  return (
    <CharterDraftCard
      // ``key`` forces a fresh mount when a new charter arrives or its version
      // bumps, so the card's local brief / amount state re-initialises from the
      // refreshed prop instead of carrying stale edits across the swap.
      key={`${draftCharter.id}:${draftCharter.version}`}
      charter={draftCharter}
      busy={mutating}
      onSave={handleSave}
      onApprove={handleApprove}
      onCancel={handleCancel}
    />
  )
}
