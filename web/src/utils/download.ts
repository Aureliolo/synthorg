import { downloadArtifactContent } from '@/api/endpoints/artifacts'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'

/**
 * Trigger a browser download of in-memory text content via a temporary
 * anchor element. Used for client-rendered exports (e.g. workflow YAML)
 * where the payload is already a string in hand.
 */
export function downloadTextFile(
  content: string,
  filename: string,
  mimeType = 'text/plain',
): void {
  // Defence-in-depth: filenames are often built from server-supplied
  // names (workflow/role titles). Browsers normalise `a.download`, but
  // strip path separators and cap the length here so a hostile name
  // cannot produce a path-bearing or unwieldy download prompt.
  const safeName = filename.replace(/[/\\]+/g, '_').slice(0, 200).trim() || 'download'
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = safeName
  try {
    document.body.appendChild(a)
    a.click()
  } finally {
    if (a.parentNode) document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }
}

/**
 * Download artifact content as a file via a temporary anchor element.
 *
 * Shows an error toast on failure.
 */
export async function downloadArtifactFile(artifactId: string, fallbackName: string): Promise<void> {
  try {
    const blob = await downloadArtifactContent(artifactId)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fallbackName
    try {
      document.body.appendChild(a)
      a.click()
    } finally {
      if (a.parentNode) document.body.removeChild(a)
      URL.revokeObjectURL(url)
    }
  } catch (err) {
    useToastStore.getState().add({
      variant: 'error',
      title: 'Download failed',
      description: getErrorMessage(err),
    })
  }
}
