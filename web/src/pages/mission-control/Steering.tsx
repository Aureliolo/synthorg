import { Compass, Send } from 'lucide-react'
import { useState } from 'react'

import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { SectionCard } from '@/components/ui/section-card'
import { useSteeringData } from '@/hooks/useSteeringData'
import { useSteeringStore } from '@/stores/steering'

import { SteeringDirectiveList } from './SteeringDirectiveList'
import { SteeringIssueForm } from './SteeringIssueForm'
import { SteeringProposalReview } from './SteeringProposalReview'

export interface SteeringProps {
  initialProjectId?: string | null
}

export function Steering({ initialProjectId }: SteeringProps) {
  const [projectId, setProjectId] = useState(initialProjectId ?? '')
  const directives = useSteeringStore((s) => s.directives)
  const loading = useSteeringStore((s) => s.directivesLoading)
  const error = useSteeringStore((s) => s.directivesError)
  const pendingProposal = useSteeringStore((s) => s.pendingProposal)

  useSteeringData(projectId)

  const hasProject = projectId.trim() !== ''

  return (
    <div className="space-y-section-gap">
      <InputField
        label="Project"
        placeholder="Project to steer (e.g. checkout)"
        value={projectId}
        onValueChange={setProjectId}
        hint="Every in-flight and newly-spawned agent on this project adopts the directive."
      />

      {error != null && hasProject && (
        <ErrorBanner
          variant="inline"
          title="Failed to load active directives"
          description={error}
        />
      )}

      {hasProject && (
        <SectionCard title="Issue a directive" icon={Send}>
          <SteeringIssueForm projectId={projectId} />
        </SectionCard>
      )}

      {pendingProposal != null && hasProject && (
        <SteeringProposalReview
          key={pendingProposal.directive_id}
          projectId={projectId}
          proposal={pendingProposal}
        />
      )}

      {hasProject && (
        <SectionCard title="Active directives" icon={Compass}>
          <SteeringDirectiveList directives={directives} loading={loading} />
        </SectionCard>
      )}
    </div>
  )
}
