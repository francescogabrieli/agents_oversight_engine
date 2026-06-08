import { useEffect, useState } from "react";

type PollState<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
};

export function usePolledResource<T>(
  loader: () => Promise<T>,
  dependencies: readonly unknown[],
  intervalMs = 2000,
): PollState<T> & { refresh: () => Promise<void> } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    try {
      const nextData = await loader();
      setData(nextData);
      setError(null);
    } catch (loaderError) {
      setError(loaderError instanceof Error ? loaderError.message : "Unknown error.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    const intervalHandle = window.setInterval(() => {
      void refresh();
    }, intervalMs);
    return () => window.clearInterval(intervalHandle);
    // The caller decides when the loader must be recreated.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);

  return { data, error, loading, refresh };
}
