/** Production Support - Overview (S1). Every customer, built from data the system already
 *  keeps (backend api/v1/support.py). Application health, stage timings / SLA and the activity
 *  log come in later phases. */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import * as supportApi from "../../api/support";
import type { IssueTab, SupportIssues, SupportOverview } from "../../api/support";
import { IssuesTable } from "./IssuesTable";

type Period = "today" | "7d" | "month";
const PERIODS: { key: Period; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "7d", label: "Last 7 days" },
  { key: "month", label: "This month" },
];

function istToday(): Date {
  const now = new Date();
  return new Date(now.getTime() + (now.getTimezoneOffset() + 330) * 60000);
}

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function rangeFor(p: Period): [string, string] {
  const t = istToday();
  if (p === "today") return [iso(t), iso(t)];
  if (p === "7d") return [iso(new Date(t.getFullYear(), t.getMonth(), t.getDate() - 6)), iso(t)];
  return [iso(new Date(t.getFullYear(), t.getMonth(), 1)), iso(t)];
}

function Tile({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone: string }) {
  return (
    <div className={`rounded-2xl border p-4 ${tone}`}>
      <p className="text-2xl font-bold tabular-nums">{value}</p>
      <p className="text-sm font-medium">{label}</p>
      {sub && <p className="text-xs opacity-75">{sub}</p>}
    </div>
  );
}

function VolumeBars({ data }: { data: SupportOverview["volume"] }) {
  const shown = data.slice(-14);
  const max = Math.max(1, ...shown.map((d) => d.jobs));
  if (shown.length === 0) return null;
  return (
    <div className="flex h-40 items-end gap-2">
      {shown.map((d) => (
        <div key={d.day} className="flex flex-1 flex-col items-center justify-end gap-1" title={`${d.day}: ${d.jobs} jobs, ${d.pages} pages`}>
          <span className="text-xs font-semibold tabular-nums text-slate-700 dark:text-slate-200">{d.jobs}</span>
          <div className="w-full rounded-t bg-sky-500" style={{ height: `${Math.max(2, (d.jobs / max) * 110)}px` }} />
          <span className="text-[10px] text-slate-500">{d.day.slice(8)}/{d.day.slice(5, 7)}</span>
        </div>
      ))}
    </div>
  );
}

export function SupportOverviewPage() {
  const [period, setPeriod] = useState<Period>("7d");
  const [data, setData] = useState<SupportOverview | null>(null);
  const [issues, setIssues] = useState<SupportIssues | null>(null);
  const [tab, setTab] = useState<IssueTab>("all");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [from, to] = rangeFor(period);
      const [o, i] = await Promise.all([supportApi.getOverview(from, to), supportApi.getIssues(tab)]);
      setData(o);
      setIssues(i);
    } catch (err) {
      setError(axios.isAxiosError(err) ? (err.response?.data?.detail ?? "Could not load the overview.") : "Could not load the overview.");
    }
  }, [period, tab]);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 60000);
    return () => window.clearInterval(t);
  }, [load]);

  const t = data?.tiles;
  return (
    <AppShell title="Production Support" subtitle="Every customer's jobs - what is moving, what failed, what needs action.">
      <div className="space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex gap-2">
            {PERIODS.map((p) => (
              <button key={p.key} type="button" onClick={() => setPeriod(p.key)}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium ${period === p.key ? "bg-indigo-600 text-white" : "border border-slate-200 text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300"}`}>
                {p.label}
              </button>
            ))}
          </div>
          <button type="button" onClick={load} className="text-sm text-indigo-700 underline dark:text-indigo-300">Refresh</button>
        </div>
        {error && <Alert>{error}</Alert>}

        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          <Tile label="Total Jobs" value={t?.total_jobs ?? "—"} tone="border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-500/20 dark:bg-sky-500/10 dark:text-sky-100" />
          <Tile label="Total Pages" value={t?.pages ?? "—"} sub={t?.avg_pages != null ? `Avg ${t.avg_pages} pages/job` : undefined}
            tone="border-violet-200 bg-violet-50 text-violet-900 dark:border-violet-500/20 dark:bg-violet-500/10 dark:text-violet-100" />
          <Tile label="Attachments" value={t?.attachments ?? "—"} tone="border-teal-200 bg-teal-50 text-teal-900 dark:border-teal-500/20 dark:bg-teal-500/10 dark:text-teal-100" />
          <Tile label="Submitted to ERP" value={t?.submitted ?? "—"} sub={t?.submitted_pct != null ? `${t.submitted_pct}%` : undefined}
            tone="border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-100" />
          <Tile label="Failed" value={t?.failed ?? "—"} sub={t?.failed_pct != null ? `${t.failed_pct}%` : undefined}
            tone="border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-100" />
        </div>

        <Card className="p-5">
          <h2 className="mb-3 font-semibold text-slate-900 dark:text-slate-50">
            Job Pipeline <span className="text-sm font-normal text-slate-500">(where this period's jobs stand now)</span>
          </h2>
          <div className="flex flex-wrap items-stretch gap-2">
            {(data?.pipeline ?? []).map((p, i, all) => (
              <div key={p.key} className="flex items-center gap-2">
                <div className={`min-w-28 rounded-xl px-3 py-2 text-center ${p.key === "failed" ? "bg-rose-50 text-rose-800 dark:bg-rose-500/10 dark:text-rose-200" : p.key === "erp_submitted" ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200" : "bg-slate-50 text-slate-800 dark:bg-slate-800 dark:text-slate-100"}`}>
                  <p className="text-xl font-bold tabular-nums">{p.count}</p>
                  <p className="text-xs">{p.label}</p>
                </div>
                {i < all.length - 2 && <span className="text-slate-400">→</span>}
              </div>
            ))}
          </div>
        </Card>

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <Card className="p-5">
            <h2 className="mb-3 font-semibold text-slate-900 dark:text-slate-50">Jobs per day</h2>
            {data && <VolumeBars data={data.volume} />}
          </Card>
          <Card className="p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold text-slate-900 dark:text-slate-50">Attention Required</h2>
              <Link to="/support/issues" className="text-sm text-indigo-700 dark:text-indigo-300">View all →</Link>
            </div>
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {(data?.attention ?? []).map((a) => (
                <li key={a.key} className="flex items-center justify-between py-2 text-sm">
                  <span className="text-slate-700 dark:text-slate-300">{a.label}</span>
                  <span className={`min-w-10 rounded-full px-2.5 py-0.5 text-center font-semibold tabular-nums ${a.count > 0 ? "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300" : "bg-slate-100 text-slate-500 dark:bg-slate-800"}`}>
                    {a.count}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-slate-500">Across all customers, jobs from the last 60 days. Delayed jobs (SLA) come in a later phase.</p>
          </Card>
        </div>

        <Card className="p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold text-slate-900 dark:text-slate-50">Failed / Delayed Jobs <span className="text-sm font-normal text-slate-500">(recent)</span></h2>
            <Link to="/support/issues" className="text-sm text-indigo-700 dark:text-indigo-300">View all →</Link>
          </div>
          <IssuesTable data={issues} tab={tab} onTab={setTab} onChanged={load} limit={8} />
        </Card>
      </div>
    </AppShell>
  );
}
