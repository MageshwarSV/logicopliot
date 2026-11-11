import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { notificationsApi, type AppNotification } from "../api/notifications";
import { BellIcon, CheckCircleIcon, ExclamationTriangleIcon } from "./ui/icons";

/** How often the bell re-checks. An ERP run takes minutes, not seconds, so anything faster is
 *  just load - and the list refreshes on open anyway, which is when it actually matters. */
const POLL_MS = 60_000;

/** "2 min ago" / "3 h ago" / "6 Nov". A notification's age is the only thing that makes it
 *  readable at a glance - an absolute timestamp has to be compared against now by hand. */
function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.floor((Date.now() - then) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days} d ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/**
 * The two things a job does after it leaves the operator: it reached the ERP, or it did not.
 *
 * Read is NOT the same as gone. Clicking one takes you to the job and marks it read, and so
 * does "Mark all as read", but the entry stays in the panel either way - greyed rather than
 * deleted. Deleting on click would mean the one way to find a failed job again is to remember
 * its number, which is exactly what someone who has just been interrupted cannot do.
 */
export function NotificationBell() {
  const navigate = useNavigate();
  const [items, setItems] = useState<AppNotification[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      setItems(await notificationsApi.list());
    } catch {
      // A bell that cannot load is not worth interrupting anyone over - it simply shows
      // nothing, and the Jobs list behind it is unaffected.
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  // Refresh on open: between polls the list can be a minute stale, and the moment someone
  // opens it is the moment that matters.
  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  // Click anywhere else closes it - a panel that can only be closed by the button it was
  // opened with sits on top of whatever you were trying to click next.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const unread = items.filter((n) => !n.read).length;

  async function openJob(n: AppNotification) {
    setOpen(false);
    // Navigate FIRST. Marking read is bookkeeping; being taken to the job is what was asked
    // for, and it must not wait on - or be lost to - a failed write.
    navigate(`/jobs/${n.job_id}`);
    try {
      if (!n.read) {
        setItems((prev) => prev.map((x) => (x.id === n.id ? { ...x, read: true } : x)));
        await notificationsApi.markRead([n.id]);
      }
    } catch {
      void load();                       // put it back the way the server sees it
    }
  }

  async function markAll() {
    setBusy(true);
    try {
      await notificationsApi.markRead();
      setItems((prev) => prev.map((x) => ({ ...x, read: true })));
    } catch {
      void load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        className="relative flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-200"
      >
        <BellIcon className="h-5 w-5" />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-600 px-1 text-[10px] font-semibold text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-96 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900">
          <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-700">
            <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">Notifications</p>
            {unread > 0 && (
              <button
                type="button"
                onClick={markAll}
                disabled={busy}
                className="text-xs font-medium text-indigo-600 hover:text-indigo-500 disabled:opacity-50 dark:text-indigo-400"
              >
                {busy ? "…" : "Mark all as read"}
              </button>
            )}
          </div>

          <div className="max-h-96 overflow-y-auto">
            {items.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-slate-400">
                Nothing yet. ERP submissions and failures show up here.
              </p>
            ) : (
              items.map((n) => {
                const failed = n.kind === "failed";
                return (
                  <button
                    key={n.id}
                    type="button"
                    onClick={() => openJob(n)}
                    className={`flex w-full items-start gap-3 border-b border-slate-100 px-4 py-3 text-left transition-colors last:border-b-0 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60 ${
                      n.read ? "opacity-60" : ""
                    }`}
                  >
                    <span
                      className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
                        failed
                          ? "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400"
                          : "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400"
                      }`}
                    >
                      {failed ? (
                        <ExclamationTriangleIcon className="h-4 w-4" />
                      ) : (
                        <CheckCircleIcon className="h-4 w-4" />
                      )}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-slate-900 dark:text-slate-100">
                        {failed ? "Failed to submit to ERP" : "Submitted to ERP"}
                      </span>
                      <span className="block truncate text-xs text-slate-500 dark:text-slate-400">
                        {n.job_reference}
                        {failed && n.detail ? ` — ${n.detail}` : ""}
                      </span>
                    </span>
                    <span className="shrink-0 whitespace-nowrap text-[11px] text-slate-400">
                      {ago(n.at)}
                    </span>
                    {/* The unread dot stays meaningful after "Mark all as read" precisely
                        because the row itself does not disappear - the list is still the
                        record of what happened, just no longer asking for attention. */}
                    {!n.read && (
                      <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-indigo-500" />
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
