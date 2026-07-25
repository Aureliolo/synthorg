import { definePreview } from '@storybook/react-vite'
import { setupWorker } from 'msw/browser'
import addonMsw from 'msw-storybook-addon'
import '../src/styles/global.css'

// The addon's own default setup warns on any request without a matching
// handler; the explicit setup below keeps 'bypass' so a story that mocks
// nothing stays silent rather than filling the console.
const startWorker = async () => {
  const worker = setupWorker()
  await worker.start({ onUnhandledRequest: 'bypass' })
  return worker
}

export default definePreview({
  addons: [addonMsw(startWorker)],
  parameters: {
    a11y: { test: 'error' },
    backgrounds: {
      options: {
        dark: { name: 'SynthOrg Dark', value: '#0a0a12' },
      },
    },
  },
  initialGlobals: {
    backgrounds: { value: 'dark' },
  },
  decorators: [
    (Story) => (
      <div className="dark bg-background p-4 text-foreground">
        <Story />
      </div>
    ),
  ],
})
