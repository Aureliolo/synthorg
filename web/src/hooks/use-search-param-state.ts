import { useCallback } from 'react'
import { useSearchParams } from 'react-router'

/**
 * One query parameter, read and written like React state.
 *
 * A filter in the URL is what lets one surface link at another's filtered view
 * rather than at the page containing it, and it survives a reload or a shared
 * address. The write is a `replace` because a filter typically changes per
 * keystroke, and pushing each one would turn Back into an undo of the query
 * instead of a way off the page.
 *
 * The empty string deletes the parameter rather than writing an empty one, so
 * clearing a filter leaves the address as it was before anyone typed.
 */
export function useSearchParamState(name: string): [string, (next: string) => void] {
  const [searchParams, setSearchParams] = useSearchParams()
  const value = searchParams.get(name) ?? ''

  const setValue = useCallback(
    (next: string) => {
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev)
          if (next === '') params.delete(name)
          else params.set(name, next)
          return params
        },
        { replace: true },
      )
    },
    [setSearchParams, name],
  )

  return [value, setValue]
}
