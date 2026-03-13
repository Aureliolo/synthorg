import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import ConfirmationService from 'primevue/confirmationservice'

import App from './App.vue'
import { router } from './router'
import { primeVueOptions } from './primevue-preset'
import './styles/global.css'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(PrimeVue, primeVueOptions)
app.use(ToastService)
app.use(ConfirmationService)

/** Sanitize a value for safe logging (strip control chars, truncate). */
function sanitizeForLog(value: unknown, maxLen = 500): string {
  if (value instanceof Error) {
    return value.message.replace(/[\x00-\x1f\x7f]/g, ' ').slice(0, maxLen)
  }
  return String(value).replace(/[\x00-\x1f\x7f]/g, ' ').slice(0, maxLen)
}

// Global error handler for unhandled errors in components
app.config.errorHandler = (err, _instance, info) => {
  console.error('Unhandled Vue error:', sanitizeForLog(err), 'Info:', sanitizeForLog(info))
}

// Catch unhandled promise rejections
window.addEventListener('unhandledrejection', (event) => {
  console.error('Unhandled promise rejection:', sanitizeForLog(event.reason))
})

app.mount('#app')
