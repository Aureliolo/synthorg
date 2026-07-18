/**
 * Minimal POST-SSE frame reader over a `fetch` `ReadableStream`.
 *
 * `EventSource` only issues GET requests, so a POST endpoint that streams
 * `text/event-stream` (the unified turn, the model-pull progress) is consumed
 * with `fetch` + a stream reader instead. This helper owns the reader lifecycle
 * and the line buffering, invoking `onFrame(event, data)` once per `data:` line
 * with the most recent `event:` name. It always cancels + releases the reader
 * (the active-handle gate tracks it), on both the clean and the throwing path.
 */
export async function readSseFrames(
  response: Response,
  onFrame: (event: string, data: string) => void,
): Promise<void> {
  const body = response.body
  if (!body) throw new Error('Expected a streaming response body')
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = ''
  const flush = (line: string): void => {
    if (line.startsWith('event: ')) {
      currentEvent = line.slice(7).trim()
    } else if (line.startsWith('data: ')) {
      onFrame(currentEvent, line.slice(6))
      currentEvent = ''
    }
  }
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) flush(line)
    }
    buffer += decoder.decode()
    if (buffer.trim()) for (const line of buffer.split('\n')) flush(line)
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}
