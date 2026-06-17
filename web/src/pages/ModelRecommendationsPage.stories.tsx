import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse } from 'msw'
import ModelRecommendationsPage from './ModelRecommendationsPage'
import { recommendationsHandlers } from '@/mocks/handlers/recommendations'
import { successFor } from '@/mocks/handlers'
import type { listModelRecommendations } from '@/api/endpoints/recommendations'

const meta = {
  title: 'Pages/ModelRecommendationsPage',
  component: ModelRecommendationsPage,
  parameters: { layout: 'fullscreen', msw: { handlers: recommendationsHandlers } },
  decorators: [(Story) => <div className="mx-auto max-w-4xl p-6"><Story /></div>],
} satisfies Meta<typeof ModelRecommendationsPage>

export default meta
type Story = StoryObj<typeof meta>

export const WithRecommendations: Story = {}

export const Empty: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get('/api/v1/providers/model-refresh/recommendations', () =>
          HttpResponse.json(successFor<typeof listModelRecommendations>([])),
        ),
        ...recommendationsHandlers,
      ],
    },
  },
}
