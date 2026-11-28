// Load data for a screen, reload when it comes back into view, pull-to-refresh, and every
// 60 s while it is open.
import { useFocusEffect } from "expo-router";
import { useCallback, useRef, useState } from "react";

import { api } from "./api";

export function useLoad<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const alive = useRef(true);

  const load = useCallback(async (pull = false) => {
    if (pull) setRefreshing(true);
    try {
      const d = await api<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setRefreshing(false);
    }
  }, [path]);

  useFocusEffect(
    useCallback(() => {
      alive.current = true;
      load();
      const t = setInterval(() => load(), 60000);
      return () => {
        alive.current = false;
        clearInterval(t);
      };
    }, [load]),
  );

  return { data, error, refreshing, reload: () => load(true) };
}
