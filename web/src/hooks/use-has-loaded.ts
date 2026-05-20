import { useRef } from 'react';

/**
 * Returns true until loading has completed at least once.
 * Prevents empty-state flash on initial mount / workspace switch.
 */
export function useInitialLoading(loading: boolean): boolean {
  const hasLoaded = useRef(false);
  if (!loading) hasLoaded.current = true;
  return !hasLoaded.current;
}
