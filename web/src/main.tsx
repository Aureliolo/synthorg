import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { installGlobalErrorHandlers } from './lib/global-error-handlers'
import { ensureFreshAppState } from './utils/app-version'
import './styles/global.css'

// Install window-level error handlers BEFORE the bootstrap promise so
// a failure in ``ensureFreshAppState`` itself surfaces via the
// structured logger and the production toast rather than vanishing
// into the browser's default ``Uncaught (in promise)`` console line.
installGlobalErrorHandlers()

// Gate boot on the build-id check so a stale csrf_token / session cookie
// from an older version is cleared before any React code (or API call)
// observes it. On mismatch this triggers a logout + storage wipe +
// reload; on match or first load it resolves immediately.
await ensureFreshAppState()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
