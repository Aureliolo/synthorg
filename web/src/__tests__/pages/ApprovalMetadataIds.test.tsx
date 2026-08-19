import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { ApprovalDetailContent } from '@/pages/approvals/ApprovalDetailContent'
import { visibleMetadataEntries } from '@/utils/approvals'
import { makeApproval } from '../helpers/factories'

/**
 * The approval drawer never prints a database key at an operator.
 *
 * The invariant is about the SHAPE of the surface, not about one producing
 * feature: approval metadata is a backend-controlled open map, so a section
 * that renders it whole prints whatever keys the next feature stamps. A hire
 * approval carries `request_id` and `candidate_id`; both reached the drawer as
 * raw UUIDs. The ids still travel with the approval and still drive the deep
 * links, so what is asserted here is only that they are not rendered as text.
 */
describe('approval metadata hides database keys', () => {
  it('drops reference-shaped keys', () => {
    expect(
      visibleMetadataEntries({
        request_id: '0f8e6f56-4d18-4c3f-9a48-0a6d1a2f0e11',
        candidate_id: '5f2c1c1a-9c4a-4f7e-9f6a-2b7d0f6a1c22',
        safety_classification: 'suspicious',
      }),
    ).toEqual([['safety_classification', 'suspicious']])
  })

  it('keeps a reference that is the word a person reads', () => {
    expect(visibleMetadataEntries({ model_id: 'example-capable-001' })).toEqual([
      ['model_id', 'example-capable-001'],
    ])
  })

  it('hides a key nobody has declared readable yet', () => {
    expect(visibleMetadataEntries({ forecastId: 'af31' })).toEqual([])
  })

  it('does not render the ids in the drawer', () => {
    render(
      <MemoryRouter>
        <ApprovalDetailContent
          approval={makeApproval('a1', {
            action_type: 'org:hire',
            metadata: {
              request_id: '0f8e6f56-4d18-4c3f-9a48-0a6d1a2f0e11',
              candidate_id: '5f2c1c1a-9c4a-4f7e-9f6a-2b7d0f6a1c22',
            },
          })}
          confidenceLabel={null}
        />
      </MemoryRouter>,
    )
    expect(screen.queryByText(/0f8e6f56-4d18-4c3f-9a48-0a6d1a2f0e11/)).not.toBeInTheDocument()
    expect(screen.queryByText(/5f2c1c1a-9c4a-4f7e-9f6a-2b7d0f6a1c22/)).not.toBeInTheDocument()
    expect(screen.queryByText('Metadata')).not.toBeInTheDocument()
  })
})
