import { useEffect, useState } from "react";
import * as settingsApi from "../../api/systemSettings";
import type { QueuesStatus } from "../../api/systemSettings";

function age(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)} h ${Math.floor((seconds % 3600) / 60)} min`;
}

/** Platform Settings -> Queues & workers: what every queue holds right now and which servers are
 *  working them (their heartbeats). Read-only; refreshed every 5 seconds. */
export function QueuesSection() {
  const [status, setStatus] = useState<QueuesStatus | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const got = await settingsApi.getQueuesStatus();
        if (alive) setStatus(got);
      } catch {
        // Nothing shown is better than a red error over a status panel.
      }
      if (alive) timer = setTimeout(tick, 5000);
    };
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, []);

  if (!status) return null;
  const th = "px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-slate-400";
  const td = "px-3 py-2 text-sm text-slate-800 dark:text-slate-100";

  return (
    <div className="border-t border-slate-200 px-5 py-4 dark:border-slate-800">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">Queues &amp; workers</h3>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        What is waiting in each queue, how long the oldest item has waited, and which server is
        working it. Refreshes every 5 seconds.
      </p>
      {!status.redis ? (
        <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:bg-slate-800/50 dark:text-slate-300">
          No Redis on this server - work runs in-process, so there are no queues to show.
        </p>
      ) : (
        <>
          {status.warnings.length > 0 && (
            <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
              {status.warnings.map((w) => (
                <div key={w}>⚠ {w}</div>
              ))}
            </div>
          )}
          <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
            <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
              <thead>
                <tr>
                  <th className={th}>Queue</th>
                  <th className={th}>Waiting</th>
                  <th className={th}>Running</th>
                  <th className={th}>Oldest waiting</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {status.queues.map((q) => (
                  <tr key={q.name}>
                    <td className={td}>{q.label}</td>
                    <td className={td}>{q.waiting}</td>
                    <td className={td}>{q.running}</td>
                    <td className={q.stale ? `${td} font-semibold text-amber-700 dark:text-amber-400` : td}>
                      {age(q.oldest_wait_seconds)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
            <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
              <thead>
                <tr>
                  <th className={th}>Server</th>
                  <th className={th}>Role</th>
                  <th className={th}>Mail workers</th>
                  <th className={th}>Extraction workers</th>
                  <th className={th}>ERP workers</th>
                  <th className={th}>Last seen</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {status.servers.length === 0 ? (
                  <tr>
                    <td className={td} colSpan={6}>No server has reported yet.</td>
                  </tr>
                ) : (
                  status.servers.map((s) => (
                    <tr key={`${s.host}-${s.role}`}>
                      <td className={td}>{s.host}</td>
                      <td className={td}>{s.role}</td>
                      <td className={td}>{s.mail_workers}{s.mail_polling ? " · polling" : ""}</td>
                      <td className={td}>{s.extract_workers}</td>
                      <td className={td}>{s.erp_workers}</td>
                      <td className={s.seen_seconds_ago > 90 ? `${td} text-amber-700 dark:text-amber-400` : td}>
                        {age(s.seen_seconds_ago)} ago
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
