import { defineMain } from '@storybook/react-vite/node'

export default defineMain({
  stories: ['../src/**/*.stories.@(ts|tsx)'],
  framework: '@storybook/react-vite',
  addons: [
    '@storybook/addon-docs',
    '@storybook/addon-a11y',
  ],
  features: {
    changeDetection: true,
  },
  async viteFinal(config) {
    const { default: tailwindcss } = await import('@tailwindcss/vite')
    config.plugins = [...(config.plugins ?? []), tailwindcss()]
    return config
  },
})
