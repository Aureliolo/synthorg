import Aura from '@primevue/themes/aura'
import type { PrimeVueConfiguration } from 'primevue'

export const primeVueOptions: PrimeVueConfiguration = {
  theme: {
    preset: Aura,
    options: {
      darkModeSelector: '.dark',
      cssLayer: false,
    },
  },
}
