import { useEffect, useRef, useState } from "react";

type PollState<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
  updatedAt: string | null;
};

export function usePolledResource<T>(
  loader: () => Promise<T>,
  dependencies: readonly unknown[],
  intervalMs = 2000,
): PollState<T> & { refresh: () => Promise<void> } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const generation = useRef(0);
  const pendingGeneration = useRef<number | null>(null);

  const refresh = async () => {
    const current = generation.current;
    if (pendingGeneration.current === current) return;
    pendingGeneration.current = current;
    try {
      const nextData = await loader();
      if (current !== generation.current) return;
      setData(nextData);
      setError(null);
      setUpdatedAt(new Date().toISOString());
    } catch (loaderError) {
      if (current !== generation.current) return;
      setError(loaderError instanceof Error ? loaderError.message : "Unknown error.");
    } finally {
      if (current === generation.current) {
        pendingGeneration.current = null;
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    generation.current += 1;
    setData(null);
    setLoading(true);
    setError(null);
    setUpdatedAt(null);
    void refresh();
    const intervalHandle = window.setInterval(() => {
      void refresh();
    }, intervalMs);
    return () => { generation.current += 1; window.clearInterval(intervalHandle); };
    // The caller decides when the loader must be recreated.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);

  return { data, error, loading, updatedAt, refresh };
}
