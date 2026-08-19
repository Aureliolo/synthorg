import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'

import type { listApprovals } from '@/api/endpoints/approvals'
import type { Plan } from '@/api/types/plans'
import { emptyPage, paginatedFor, successFor } from '@/mocks/handlers/helpers'
import { PlanOpenQuestionsPanel } from '@/pages/plans/PlanOpenQuestionsPanel'
import { server } from '@/test-setup'

import { makeApproval, makePlan, makePlanItem } from '../../helpers/factories'

const A_QUESTION = 'Which persistence backend?'

/** The approval the planner parks so a person can answer one question. */
function parkedQuestion(id: string, question: string) {
  return makeApproval(id, {
    source: 'plan_review',
    status: 'pending',
    action_type: 'clarify:question',
    description: question,
    metadata: { plan_id: 'p' },
  })
}

function servingParkedQuestions(...parked: ReturnType<typeof parkedQuestion>[]) {
  server.use(
    http.get('/api/v1/approvals', () =>
      HttpResponse.json(
        paginatedFor<typeof listApprovals>({ ...emptyPage(), data: parked }),
      ),
    ),
  )
}

function renderPanel(plan: Plan) {
  return render(
    <MemoryRouter>
      <PlanOpenQuestionsPanel plan={plan} />
    </MemoryRouter>,
  )
}

describe('PlanOpenQuestionsPanel', () => {
  it('renders nothing when the plan surfaced no questions or assumptions', () => {
    const { container } = renderPanel(makePlan('p'))
    expect(container).toBeEmptyDOMElement()
  })

  it('lists open questions with a count and the plan assumptions', () => {
    const plan = makePlan('p', {
      open_questions: ['Which persistence backend?', 'Is offline play in scope?'],
      assumptions: ['Single-player only for v1'],
    })
    renderPanel(plan)
    expect(screen.getByText('Needs your input')).toBeInTheDocument()
    expect(screen.getByText('2 open questions')).toBeInTheDocument()
    expect(screen.getByText('Which persistence backend?')).toBeInTheDocument()
    expect(screen.getByText('Single-player only for v1')).toBeInTheDocument()
  })

  it('shows assumptions alone without an open-question count', () => {
    renderPanel(makePlan('p', { assumptions: ['Metric units throughout'] }))
    expect(screen.getByText('Needs your input')).toBeInTheDocument()
    expect(screen.queryByText(/open question/)).not.toBeInTheDocument()
    expect(screen.getByText('Metric units throughout')).toBeInTheDocument()
  })

  it('answers a parked question where the question is read', async () => {
    // The Approvals inbox excludes every plan_review row by design, so this
    // panel is the only surface that can decide one. Sent anywhere else, the
    // question stays pending until the plan builds and expires it unanswered.
    const answered = vi.fn()
    servingParkedQuestions(parkedQuestion('q-1', A_QUESTION))
    server.use(
      http.post('/api/v1/approvals/q-1/approve', async ({ request }) => {
        answered((await request.json()) as { comment?: string })
        return HttpResponse.json(
          successFor<() => Promise<unknown>>(parkedQuestion('q-1', A_QUESTION)),
        )
      }),
    )
    renderPanel(makePlan('p', { open_questions: [A_QUESTION] }))

    const box = await screen.findByLabelText(/your answer/i)
    await userEvent.type(box, 'SQLite')
    await userEvent.click(screen.getByRole('button', { name: /send answer/i }))

    // The answer travels as the approval comment, which is what the backend
    // writes onto the plan the agents execute.
    await waitFor(() => {
      expect(answered).toHaveBeenCalledWith({ comment: 'SQLite' })
    })
  })

  it('says a question is closed rather than offering an answer that settles nothing', async () => {
    // Once the plan starts building, its questions are settled by omission and
    // the parked approvals expire. An answer box there would take an answer
    // that reaches no task, no agent and no prompt.
    servingParkedQuestions()
    renderPanel(makePlan('p', { open_questions: [A_QUESTION] }))

    expect(await screen.findByText(/no longer answerable/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/your answer/i)).not.toBeInTheDocument()
  })

  it('answers only the question the operator was reading', async () => {
    // Two questions are parked on one plan under the same source and plan id;
    // matching on the plan alone would settle whichever the API ordered first.
    const answered = vi.fn()
    const other = 'Is offline play in scope?'
    servingParkedQuestions(
      parkedQuestion('q-other', other),
      parkedQuestion('q-1', A_QUESTION),
    )
    server.use(
      http.post('/api/v1/approvals/:id/approve', ({ params }) => {
        answered(params['id'])
        return HttpResponse.json(
          successFor<() => Promise<unknown>>(parkedQuestion('q-1', A_QUESTION)),
        )
      }),
    )
    renderPanel(makePlan('p', { open_questions: [A_QUESTION, other] }))

    const boxes = await screen.findAllByLabelText(/your answer/i)
    await userEvent.type(boxes[0]!, 'SQLite')
    await userEvent.click(screen.getAllByRole('button', { name: /send answer/i })[0]!)

    await waitFor(() => {
      expect(answered).toHaveBeenCalledWith('q-1')
    })
  })

  it('offers a box for a question parked while the page stayed open', async () => {
    // Rework and replan both park questions onto a plan already on screen,
    // over the live channel, and the route element is reused rather than
    // remounted. A lookup keyed on the plan id alone never re-runs, so the
    // new question renders as unanswerable while the plan waits on it.
    const asking = makePlan('p', { open_questions: [A_QUESTION] })
    servingParkedQuestions(parkedQuestion('q-1', A_QUESTION))
    const { rerender } = renderPanel(asking)
    await screen.findByLabelText(/your answer/i)

    const later = 'Which runtime is allowed?'
    servingParkedQuestions(
      parkedQuestion('q-1', A_QUESTION),
      parkedQuestion('q-2', later),
    )
    rerender(
      <MemoryRouter>
        <PlanOpenQuestionsPanel
          plan={makePlan('p', { open_questions: [A_QUESTION, later] })}
        />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getAllByLabelText(/your answer/i)).toHaveLength(2)
    })
    expect(screen.queryByText(/no longer answerable/i)).not.toBeInTheDocument()
  })

  it('answers two questions at once without stranding either control', async () => {
    // One shared "which id is submitting" is reassigned by the second send,
    // which re-enables the first row mid-request; and a completion guarded on
    // the lookup generation is dropped whenever a sibling finishes first.
    const second = 'Is offline play in scope?'
    const plan = makePlan('p', { open_questions: [A_QUESTION, second] })
    servingParkedQuestions(
      parkedQuestion('q-1', A_QUESTION),
      parkedQuestion('q-2', second),
    )
    const settled: string[] = []
    server.use(
      http.post('/api/v1/approvals/:id/approve', ({ params }) => {
        settled.push(String(params['id']))
        return HttpResponse.json(
          successFor<() => Promise<unknown>>(
            parkedQuestion(String(params['id']), A_QUESTION),
          ),
        )
      }),
    )
    renderPanel(plan)

    const boxes = await screen.findAllByLabelText(/your answer/i)
    await userEvent.type(boxes[0]!, 'SQLite')
    await userEvent.type(boxes[1]!, 'Out of scope')
    const sends = screen.getAllByRole('button', { name: /send answer/i })
    await userEvent.click(sends[0]!)
    await userEvent.click(sends[1]!)

    await waitFor(() => {
      expect(settled).toContain('q-1')
      expect(settled).toContain('q-2')
    })
    // Neither row is left showing progress for an answer that already landed.
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /sending/i })).not.toBeInTheDocument()
    })
  })

  it('stops asking a question the plan already settles', () => {
    const plan = makePlan('p', {
      open_questions: ['Which persistence backend?', 'Is offline play in scope?'],
      items: [
        makePlanItem('i1', {
          title: 'Storage layer',
          acceptance_criteria: ['The persistence backend is SQLite'],
        }),
      ],
    })
    renderPanel(plan)

    // Only the genuinely open one counts, and only it gets the ask.
    expect(screen.getByText('1 open question')).toBeInTheDocument()
    expect(screen.getByText('Already answered by the plan')).toBeInTheDocument()
    // Separated, not deleted: a wrong match must cost a glance rather than a
    // question the operator never got to answer.
    expect(screen.getByText(/settled by Storage layer/)).toBeInTheDocument()
  })
})
