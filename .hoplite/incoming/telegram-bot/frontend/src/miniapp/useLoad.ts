import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { errText } from "../shared/api";

export function useLoad<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const load = useCallback(async () => {
    if (path === null) return;
    setLoading(true);
    setError(null);
    try { setData(await api<T>("GET", path)); } catch (e) { setError(errText(e)); }
    setLoading(false);
  }, [path, ...deps]);
  useEffect(() => { void load(); }, [load]);
  return { data, error, loading, reload: load, setData };
}
