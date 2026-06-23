import { useEffect, useState } from "react";

/**
 * Tracks the user's `prefers-reduced-motion` setting.
 *
 * The initial state defaults to `true` (the conservative choice) so the
 * server-rendered markup of these islands never animates before hydration; a
 * reduced-motion user therefore never sees a first-paint frame of animation.
 * The effect syncs to the real preference and registers the change listener
 * once the client `matchMedia` is available.
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(true);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReduced(mq.matches);
    sync();
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return reduced;
}
