import { useEffect, useMemo, useState, type ReactNode } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import { Avatar } from "../../components/ui/Avatar";
import {
  CalendarIcon, ChartBarIcon, CheckBadgeIcon, ClockIcon, DownloadIcon, ExclamationTriangleIcon, LayersIcon, PlaneIcon, ShipIcon,
} from "../../components/ui/icons";
import { FIELD_CLASS, OUTLINE_BTN } from "../../components/tenant-admin/masterDataUi";
import * as reportsApi from "../../api/reports";
import type { UserJobSummary } from "../../api/reports";

type Period = "today" | "yesterday" | "this_month" | "last_month" | "custom";

const PERIODS: { key: Period; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "this_month", label: "This Month" },
  { key: "last_month", label: "Last Month" },
];

/** "YYYY-MM-DD" in the viewer's own timezone (never toISOString - that is UTC). */
function dayKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function rangeFor(period: Exclude<Period, "custom">): { from: string; to: string } {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  switch (period) {
    case "today":
      return { from: dayKey(today), to: dayKey(today) };
    case "yesterday": {
      const y = new Date(today);
      y.setDate(y.getDate() - 1);
      return { from: dayKey(y), to: dayKey(y) };
    }
    case "this_month":
      return { from: dayKey(new Date(today.getFullYear(), today.getMonth(), 1)), to: dayKey(today) };
    case "last_month":
      return {
        from: dayKey(new Date(today.getFullYear(), today.getMonth() - 1, 1)),
        to: dayKey(new Date(today.getFullYear(), today.getMonth(), 0)),
      };
  }
}

function modeIcon(mode: string) {
  return mode.startsWith("Air") ? <PlaneIcon className="h-4 w-4" /> : mode.startsWith("Sea") ? <ShipIcon className="h-4 w-4" /> : <LayersIcon className="h-4 w-4" />;
}

function errorText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) ? err.response?.data?.detail ?? fallback : fallback;
}

/** Reports -> User Job Summary (Tenant Admin). Per user, for one operation and period: jobs
 *  that arrived, how many reached the ERP, and how many are still pending. */
export function UserJobSummaryPage() {
  // "" = All operations (the default); otherwise one operation, e.g. "Sea Import".
  const [mode, setMode] = useState<string>("");
  const [period, setPeriod] = useState<Period>("today");
  const [custom, setCustom] = useState(() => rangeFor("this_month"));
  const [data, setData] = useState<UserJobSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const range = useMemo(() => (period === "custom" ? custom : rangeFor(period)), [period, custom]);
  const customInvalid = period === "custom" && (!custom.from || !custom.to || custom.to < custom.from);
  const params = useMemo(() => ({ mode: mode || undefined, date_from: range.from, date_to: range.to }), [mode, range]);

  useEffect(() => {
    if (customInvalid) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    reportsApi
      .getUserJobSummary(params)
      .then((d) => {
        if (cancelled) return;
        setData(d);
      })
      .catch((err) => !cancelled && setError(errorText(err, "Could not load the report.")))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, customInvalid]);

  async function exportReport() {
    setExporting(true);
    try {
      await reportsApi.downloadUserJobSummary(params);
    } catch (err) {
      setError(errorText(err, "Could not export the report."));
    } finally {
      setExporting(false);
    }
  }

  const totals = data?.totals ?? { total: 0, completed: 0, failed: 0, pending: 0 };
  const showData = Boolean(data) && (data?.mode ?? "") === mode;

  return (
    <AppShell
      title="Reports – User Job Summary"
      subtitle="Track user-wise job progress for the selected operation and period."
      icon={<ChartBarIcon className="h-6 w-6" />}
    >
      <div className="space-y-5">
        {error && <Alert>{error}</Alert>}

        <Card className="flex flex-wrap items-center gap-x-6 gap-y-3 px-5 py-4">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">Operation</span>
            <div className="flex flex-wrap gap-1 rounded-xl border border-slate-200 p-1 dark:border-slate-700">
              <Segment active={mode === ""} onClick={() => setMode("")} strong>
                <LayersIcon className="h-4 w-4" /> All
              </Segment>
              {(data?.modes ?? []).map((m) => (
                <Segment key={m} active={mode === m} onClick={() => setMode(m)} strong>
                  {modeIcon(m)} {m}
                </Segment>
              ))}
            </div>
          </div>
          <span className="hidden h-8 w-px bg-slate-200 lg:block dark:bg-slate-700" />
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">Period</span>
            <div className="flex flex-wrap gap-1">
              {PERIODS.map((p) => (
                <Segment key={p.key} active={period === p.key} onClick={() => setPeriod(p.key)}>
                  {p.label}
                </Segment>
              ))}
              <Segment active={period === "custom"} onClick={() => setPeriod("custom")}>
                <CalendarIcon className="h-4 w-4" /> Custom
              </Segment>
            </div>
            {period === "custom" && (
              <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
                <input type="date" value={custom.from} max={custom.to || undefined} onChange={(e) => setCustom((c) => ({ ...c, from: e.target.value }))} className={FIELD_CLASS} aria-label="From date" />
                <span>to</span>
                <input type="date" value={custom.to} min={custom.from || undefined} onChange={(e) => setCustom((c) => ({ ...c, to: e.target.value }))} className={FIELD_CLASS} aria-label="To date" />
              </div>
            )}
          </div>
        </Card>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <SummaryCard label="Total Jobs" value={showData ? totals.total : null} icon={<LayersIcon className="h-7 w-7" />}
            box="border-sky-200 bg-gradient-to-br from-sky-50 to-white dark:border-sky-500/20 dark:from-sky-500/10 dark:to-slate-900"
            tile="bg-sky-100 text-sky-600 dark:bg-sky-500/15 dark:text-sky-300" valueCls="text-slate-900 dark:text-slate-50" />
          <SummaryCard label="ERP Completed Jobs" value={showData ? totals.completed : null} icon={<CheckBadgeIcon className="h-7 w-7" />}
            box="border-emerald-200 bg-gradient-to-br from-emerald-50 to-white dark:border-emerald-500/20 dark:from-emerald-500/10 dark:to-slate-900"
            tile="bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300" valueCls="text-slate-900 dark:text-slate-50" />
          <SummaryCard label="Failed Jobs" value={showData ? totals.failed : null} icon={<ExclamationTriangleIcon className="h-7 w-7" />}
            box="border-rose-200 bg-gradient-to-br from-rose-50 to-white dark:border-rose-500/20 dark:from-rose-500/10 dark:to-slate-900"
            tile="bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-300" valueCls="text-rose-700 dark:text-rose-300" />
          <SummaryCard label="Pending Jobs" value={showData ? totals.pending : null} icon={<ClockIcon className="h-7 w-7" />}
            box="border-amber-200 bg-gradient-to-br from-amber-50 to-white dark:border-amber-500/20 dark:from-amber-500/10 dark:to-slate-900"
            tile="bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300" valueCls="text-amber-800 dark:text-amber-300" />
        </div>

        <Card className="p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-50">User-wise Job Summary</h2>
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Jobs that arrived {range.from === range.to ? `on ${fmt(range.from)}` : `from ${fmt(range.from)} to ${fmt(range.to)}`}
                {mode ? ` · ${mode}` : " · all operations"}. ERP Completed = reached the ERP; Failed = the ERP entry failed; Pending = not yet.
              </p>
            </div>
            <button type="button" className={OUTLINE_BTN} disabled={!showData || exporting || customInvalid} onClick={exportReport}>
              <DownloadIcon className="h-4 w-4" /> {exporting ? "Exporting…" : "Export"}
            </button>
          </div>

          {customInvalid ? (
            <p className="py-12 text-center text-sm text-slate-500">Pick a From date that is on or before the To date.</p>
          ) : loading && !showData ? (
            <p className="py-12 text-center text-sm text-slate-500">Loading…</p>
          ) : !data?.users.length ? (
            <p className="py-12 text-center text-sm text-slate-500 dark:text-slate-400">No jobs arrived for this operation in this period.</p>
          ) : (
            <div className={`overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 ${loading ? "opacity-60" : ""}`}>
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-indigo-50/70 text-slate-800 dark:bg-slate-800/60 dark:text-slate-100">
                    <th className="w-20 border-r border-slate-200 px-4 py-3 text-center font-semibold dark:border-slate-700">S.No</th>
                    <th className="border-r border-slate-200 px-4 py-3 text-left font-semibold dark:border-slate-700">User</th>
                    <th className="border-r border-slate-200 px-4 py-3 text-center font-semibold dark:border-slate-700">Total Jobs</th>
                    <th className="border-r border-slate-200 px-4 py-3 text-center font-semibold dark:border-slate-700">ERP Completed Jobs</th>
                    <th className="border-r border-slate-200 px-4 py-3 text-center font-semibold dark:border-slate-700">Failed Jobs</th>
                    <th className="px-4 py-3 text-center font-semibold">Pending Jobs</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {data.users.map((r, i) => (
                    <tr key={r.user_id ?? "unassigned"} className="hover:bg-slate-50/60 dark:hover:bg-slate-800/30">
                      <td className="border-r border-slate-100 px-4 py-2.5 text-center text-slate-600 dark:border-slate-800 dark:text-slate-300">{i + 1}</td>
                      <td className="border-r border-slate-100 px-4 py-2.5 dark:border-slate-800">
                        <div className="flex items-center gap-3">
                          <Avatar name={r.name} url={r.avatar_url} />
                          <span className={`font-medium ${r.user_id ? "text-slate-900 dark:text-slate-100" : "italic text-slate-500"}`}>{r.name}</span>
                        </div>
                      </td>
                      <td className="border-r border-slate-100 px-4 py-2.5 text-center tabular-nums text-slate-800 dark:border-slate-800 dark:text-slate-100">{r.total}</td>
                      <td className="border-r border-slate-100 px-4 py-2.5 text-center tabular-nums text-slate-800 dark:border-slate-800 dark:text-slate-100">{r.completed}</td>
                      <td className="border-r border-slate-100 px-4 py-2.5 text-center dark:border-slate-800"><FailedPill value={r.failed} /></td>
                      <td className="px-4 py-2.5 text-center"><PendingPill value={r.pending} /></td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="bg-indigo-50/70 text-base font-bold text-slate-900 dark:bg-slate-800/60 dark:text-slate-50">
                    <td colSpan={2} className="border-r border-slate-200 px-4 py-3 text-center dark:border-slate-700">Total</td>
                    <td className="border-r border-slate-200 px-4 py-3 text-center tabular-nums dark:border-slate-700">{totals.total}</td>
                    <td className="border-r border-slate-200 px-4 py-3 text-center tabular-nums dark:border-slate-700">{totals.completed}</td>
                    <td className="border-r border-slate-200 px-4 py-3 text-center dark:border-slate-700"><FailedPill value={totals.failed} large /></td>
                    <td className="px-4 py-3 text-center"><PendingPill value={totals.pending} large /></td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </Card>
      </div>
    </AppShell>
  );
}

function fmt(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

function Segment({ active, onClick, strong, children }: { active: boolean; onClick: () => void; strong?: boolean; children: ReactNode }) {
  const on = strong
    ? "bg-indigo-600 text-white shadow-sm"
    : "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200 dark:bg-indigo-500/15 dark:text-indigo-300 dark:ring-indigo-500/30";
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
        active ? on : "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
      }`}
    >
      {children}
    </button>
  );
}

function SummaryCard({ label, value, icon, box, tile, valueCls }: { label: string; value: number | null; icon: ReactNode; box: string; tile: string; valueCls: string }) {
  return (
    <div className={`flex items-center gap-5 rounded-2xl border p-5 ${box}`}>
      <div className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-xl ${tile}`}>{icon}</div>
      <div>
        <p className="text-sm font-medium text-slate-700 dark:text-slate-300">{label}</p>
        <p className={`mt-1 text-3xl font-bold tabular-nums ${valueCls}`}>{value ?? "—"}</p>
      </div>
    </div>
  );
}

function FailedPill({ value, large }: { value: number; large?: boolean }) {
  return (
    <span className={`inline-flex min-w-12 justify-center rounded-full px-3 font-semibold tabular-nums ${
      value > 0 ? "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300" : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
    } ${large ? "py-1 text-base" : "py-0.5 text-sm"}`}>
      {value}
    </span>
  );
}

function PendingPill({ value, large }: { value: number; large?: boolean }) {
  return (
    <span className={`inline-flex min-w-12 justify-center rounded-full bg-amber-100 px-3 font-semibold tabular-nums text-amber-800 dark:bg-amber-500/15 dark:text-amber-300 ${large ? "py-1 text-base" : "py-0.5 text-sm"}`}>
      {value}
    </span>
  );
}
