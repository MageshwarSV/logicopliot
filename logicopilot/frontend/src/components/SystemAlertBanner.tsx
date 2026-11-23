import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import * as settingsApi from "../api/systemSettings";

/** Super Admin only: anything that needs attention right now - the AI service unavailable
 *  (mail reading paused), a queue whose oldest item has waited too long, a queue with no worker.
 *  Read from the same snapshot as Settings -> Queues & workers; refreshed every 30 seconds.
 *  Shows nothing when all is well (or when the server has no queues). */
export function SystemAlertBanner() {
  const [warnings, setWarnings] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const s = await settingsApi.getQueuesStatus();
        if (alive) setWarnings(s.warnings ?? []);
      } catch {
        // A failed read shows nothing - never a red error over every page.
      }
      if (alive) timer = setTimeout(tick, 30000);
    };
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, []);

  if (warnings.length === 0) return null;
  return (
    <div className="border-b border-amber-200 bg-amber-50 px-8 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
      {warnings.map((w) => (
        <div key={w}>⚠ {w}</div>
      ))}
      <Link to="/super-admin/settings" className="text-xs font-medium underline">
        Open Queues &amp; workers
      </Link>
    </div>
  );
}
