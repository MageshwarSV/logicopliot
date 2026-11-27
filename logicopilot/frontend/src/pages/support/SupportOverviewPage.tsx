/** Production Support - Overview. Every customer, from data the system keeps
 *  (backend api/v1/support.py): tiles, health, pipeline, performance vs SLA, volume,
 *  attention, failed / delayed jobs and the support activity log. */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import * as supportApi from "../../api/support";
import type { IssueTab, SupportHealth, SupportIssues, SupportOverview, SupportPerformance } from "../../api/support";
import { IssuesTable } from "./IssuesTable";
import { ActivityList, PeriodPicker, STATUS_STYLE, mins, rangeFor, type Period } from "./supportUi";

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

function SectionHead({ title, to, extra }: { title: ReactNode; to?: string; extra?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h2 className="font-semibold text-slate-900 dark:text-slate-50">{title}</h2>
      <div className="flex items-center gap-3">
        {extra}
        {to && <Link to={to} className="text-sm text-indigo-700 dark:text-indigo-300">View details →</Link>}
      </div>
    </div>
  );
}

export function SupportOverviewPage() {
  const [period, setPeriod] = useState<Period>("7d");
  const [data, setData] = useState<SupportOverview | null>(null);
  const [issues, setIssues] = useState<SupportIssues | null>(null);
  const [health, setHealth] = useState<SupportHealth | null>(null);
  const [perf, setPerf] = useState<SupportPerformance | null>(null);
  const [tab, setTab] = useState<IssueTab>("all");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [from, to] = rangeFor(period);
      const [o, i, h, p] = await Promise.all([
        supportApi.getOverview(from, to), supportApi.getIssues(tab), supportApi.getHealth(), supportApi.getPerformance(from, to),
      ]);
      setData(o);
      setIssues(i);
      setHealth(h);
      setPerf(p);
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
  const overall = health ? STATUS_STYLE[health.status] : null;
  return (
    <AppShell title="Production Support" subtitle="Every customer's jobs - what is moving, what failed, what needs action.">
      <div className="space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <PeriodPicker value={period} onChange={setPeriod} />
          <button type="button" onClick={load} className="text-sm text-indigo-700 underline dark:text-indigo-300">Refresh</button>
        </div>
        {error && <Alert>{error}</Alert>}

        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Tile label="Total Jobs" value={t?.total_jobs ?? "—"} tone="border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-500/20 dark:bg-sky-500/10 dark:text-sky-100" />
          <Tile label="Total Pages" value={t?.pages ?? "—"} sub={t?.avg_pages != null ? `Avg ${t.avg_pages} pages/job` : undefined}
            tone="border-violet-200 bg-violet-50 text-violet-900 dark:border-violet-500/20 dark:bg-violet-500/10 dark:text-violet-100" />
          <Tile label="Attachments" value={t?.attachments ?? "—"} tone="border-teal-200 bg-teal-50 text-teal-900 dark:border-teal-500/20 dark:bg-teal-500/10 dark:text-teal-100" />
          <Tile label="Submitted to ERP" value={t?.submitted ?? "—"} sub={t?.submitted_pct != null ? `${t.submitted_pct}%` : undefined}
            tone="border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-100" />
          <Tile label="Failed" value={t?.failed ?? "—"} sub={t?.failed_pct != null ? `${t.failed_pct}%` : undefined}
            tone="border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-100" />
          <Tile label="Delayed (> SLA)" value={t?.delayed ?? "—"} sub={t?.delayed_pct != null ? `${t.delayed_pct}%` : undefined}
            tone="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-100" />
        </div>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
          <Card className="p-5">
            <SectionHead title="Application Health" to="/support/health"
              extra={overall && <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${overall.pill}`}>{health?.status === "ok" ? "All systems healthy" : overall.label}</span>} />
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {(health?.components ?? []).map((c) => (
                <div key={c.key} className="flex items-center justify-between rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800" title={c.detail}>
                  <span className="flex items-center gap-2 text-sm">
                    <span className={`h-2.5 w-2.5 rounded-full ${STATUS_STYLE[c.status].dot}`} />
                    {c.label}
                  </span>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[c.status].pill}`}>{STATUS_STYLE[c.status].label}</span>
                </div>
              ))}
            </div>
          </Card>
          <Card className="p-5">
            <SectionHead title={<>Job Pipeline <span className="text-sm font-normal text-slate-500">(where this period's jobs stand now)</span></>} />
            <div className="flex flex-wrap items-stretch gap-2">
              {(data?.pipeline ?? []).map((p) => (
                <Link key={p.key} to={`/support/jobs?stage=${p.key}&period=${period}`}
                  className={`min-w-24 rounded-xl px-3 py-2 text-center hover:ring-2 hover:ring-indigo-200 ${p.key === "failed" ? "bg-rose-50 text-rose-800 dark:bg-rose-500/10 dark:text-rose-200" : p.key === "erp_submitted" ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200" : "bg-slate-50 text-slate-800 dark:bg-slate-800 dark:text-slate-100"}`}>
                  <p className="text-xl font-bold tabular-nums">{p.count}</p>
                  <p className="text-xs">{p.label}</p>
                </Link>
              ))}
            </div>
          </Card>
        </div>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
          <Card className="p-5">
            <SectionHead title={<>Processing Performance <span className="text-sm font-normal text-slate-500">(avg time)</span></>} to="/support/performance" />
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {(perf?.stages ?? []).map((s) => (
                <li key={s.key} className="flex items-center justify-between py-2 text-sm">
                  <span className="text-slate-700 dark:text-slate-300">{s.label}</span>
                  <span className="flex items-center gap-2">
                    <span className={`rounded-md px-2 py-0.5 font-semibold tabular-nums ${s.avg == null ? "bg-slate-100 text-slate-500 dark:bg-slate-800" : s.ok ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300" : "bg-amber-50 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300"}`}>{mins(s.avg)}</span>
                    <span className="text-xs text-slate-500">SLA ≤ {s.sla} min</span>
                  </span>
                </li>
              ))}
            </ul>
          </Card>
          <Card className="p-5">
            <SectionHead title="Jobs per day" />
            {data && <VolumeBars data={data.volume} />}
          </Card>
          <Card className="p-5">
            <SectionHead title="Attention Required" to="/support/issues" />
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
            <p className="mt-2 text-xs text-slate-500">
              Open now, across all customers - jobs from the last {data?.attention_window_days ?? 60} days (not only this period). Handled ones are hidden.
            </p>
          </Card>
        </div>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
          <Card className="p-5 xl:col-span-2">
            <SectionHead title={<>Failed / Delayed Jobs <span className="text-sm font-normal text-slate-500">(recent)</span></>} to="/support/issues" />
            <IssuesTable data={issues} tab={tab} onTab={setTab} onChanged={load} limit={8} />
          </Card>
          <Card className="p-5">
            <SectionHead title="Recent Activity" to="/support/activity" />
            <ActivityList items={data?.activity ?? []} />
          </Card>
        </div>
      </div>
    </AppShell>
  );
}
