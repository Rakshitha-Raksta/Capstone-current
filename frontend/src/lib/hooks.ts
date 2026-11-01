import { useCallback, useEffect, useRef, useState } from 'react';

export interface Loadable<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Fetch on mount and whenever deps change. Keeps previous data while reloading. */
export function useApi<T>(fn: () => Promise<T>, deps: unknown[] = []): Loadable<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const seq = useRef(0);
  useEffect(() => {
    const my = ++seq.current;
    setLoading(true);
    fn()
      .then((d) => { if (my === seq.current) { setData(d); setError(null); } })
      .catch((e: Error) => { if (my === seq.current) setError(e.message); })
      .finally(() => { if (my === seq.current) setLoading(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

/** Poll while `active` is true. */
export function usePoll(cb: () => void, active: boolean, intervalMs = 3000) {
  useEffect(() => {
    if (!active) return undefined;
    const id = window.setInterval(cb, intervalMs);
    return () => window.clearInterval(id);
  }, [cb, active, intervalMs]);
}
