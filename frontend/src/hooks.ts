import { useCallback, useEffect, useRef, useState } from "react";

/** Load data with a reload() handle. Keeps the last good value while reloading. */
export function useLoader<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  loadRef.current = load;

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setData(undefined);
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, setData, error, loading, reload };
}

/** Run reload() every `ms` while `active` is true. */
export function usePolling(reload: () => unknown, ms: number, active = true) {
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(reload, ms);
    return () => window.clearInterval(id);
  }, [reload, ms, active]);
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  // Backend timestamps are UTC; older SQLite rows may lack the offset.
  const t = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z").getTime();
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
