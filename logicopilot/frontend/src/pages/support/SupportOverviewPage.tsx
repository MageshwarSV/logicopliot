/** Production Support - Overview. Every customer, from data the system keeps
 *  (backend api/v1/support.py): tiles, health, pipeline, performance vs SLA, volume,
 *  attention, failed / delayed jobs and the support activity log. Today by default. */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Alert } from "../../components/ui/Alert";
import {
  ArrowPathIcon, BriefcaseIcon, CheckBadgeIcon, CheckCircleIcon, ChartBarIcon, ClockIcon, DocumentIcon,
  ExclamationTriangleIcon, LayersIcon, MonitorIcon, UploadIcon,
} from "../../components/ui/icons";
import * as supportApi from "../../api/support";
import type { IssueTab, SupportHealth, SupportIssues, SupportOverview, SupportPerformance } from "../../api/support";
import { IssuesTable } from "./IssuesTable";
import {
  ActivityList, Badge, DEFAULT_PERIOD, PeriodPicker, STAGE_TONE, STATUS_STYLE, SectionCard, StatTile, mins, rangeFor, type Period,
} from "./supportUi";

const PIPE_TONE: Record<string, string> = {
  red: "bg-rose-50 text-rose-800 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-200 dark:ring-rose-500/30",
  green: "bg-emerald-50 text-emerald-800 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-200 dark:ring-emerald-500/30",
  blue: "bg-sky-50 text-sky-800 ring-sky-200 dark:bg-sky-500/10 dark:text-sky-200 dark:ring-sky-500/30",
  amber: "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-200 dark:ring-amber-500/30",
  violet: "bg-violet-50 text-violet-800 ring-violet-200 dark:bg-violet-500/10 dark:text-violet-200 dark:ring-violet-500/30",
  slate: "bg-slate-50 text-slate-800 ring-slate-200 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700",
};

function VolumeBars({ data }: { data: SupportOverview["volume"] }) {
  const shown = data.slice(-14);
  const max = Math.max(1, ...shown.map((d) => d.jobs));
  if (shown.length === 0) return null;
  return (
    <div className="flex h-44 items-end gap-2">
      {shown.map((d) => (
        <div key={d.day} className="flex flex-1 flex-col items-center justify-end gap-1" title={`${d.day}: ${d.jobs} jobs, ${d.pages} pages`}>
          <span className="text-xs font-semibold tabular-nums text-slate-700 dark:text-slate-200">{d.jobs}</span>
          <div className="w-full rounded-t-md bg-gradient-to-t from-indigo-500 to-sky-400" style={{ height: `${Math.max(3, (d.jobs / max) * 120)}px` }} />
          <span className="text-[10px] text-slate-500">{d.day.slice(8)}/{d.day.slice(5, 7)}</span>
        </div>
      ))}
    </div>
  );
}

const ATTENTION_TONE: Record<string, "red" | "orange" | "amber"> = {
  erp_failed: "red", extraction_failed: "red", erp_reports: "red", erp_slow: "orange", extraction_slow: "orange", missing_documents: "amber",
};

export function SupportOverviewPage() {
  const [period, setPeriod] = useState<Period>(DEFAULT_PERIOD);
  const [data, setData] = useState<SupportOverview | null>(null);
  const [issues, setIssues] = useState<SupportIssues | null>(null);
  const [health, setHealth] = useState<SupportHealth | null>(null);
  const [perf, setPerf] = useState<SupportPerformance | null>(null);
  const [tab, setTab] = useState<IssueTab>("all");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
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
    } finally {
      setLoading(false);
    }
  }, [period, tab]);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 60000);
    return () => window.clearInterval(t);
  }, [load]);

  const t = data?.tiles;
  const viewAll = (to: string) => <Link to={to} className="text-sm font-medium text-indigo-700 hover:underline dark:text-indigo-300">View all →</Link>;
  return (
    <AppShell title="Production Support" icon={<MonitorIcon className="h-6 w-6" />}
      subtitle="Every customer's jobs - what is moving, what failed, what needs action.">
      <div className="space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <PeriodPicker value={period} onChange={setPeriod} />
          <button type="button" onClick={load}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200">
            <ArrowPathIcon className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />Refresh
          </button>
        </div>
        {error && <Alert>{error}</Alert>}

        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <StatTile label="Total jobs" value={t?.total_jobs ?? "—"} icon={<BriefcaseIcon className="h-5 w-5" />} tone="blue" />
          <StatTile label="Pages" value={t?.pages ?? "—"} sub={t?.avg_pages != null ? `Avg ${t.avg_pages} / job` : undefined} icon={<DocumentIcon className="h-5 w-5" />} tone="violet" />
          <StatTile label="Attachments" value={t?.attachments ?? "—"} icon={<UploadIcon className="h-5 w-5" />} tone="slate" />
          <StatTile label="Submitted to ERP" value={t?.submitted ?? "—"} sub={t?.submitted_pct != null ? `${t.submitted_pct}%` : undefined} icon={<CheckCircleIcon className="h-5 w-5" />} tone="green" />
          <StatTile label="Failed" value={t?.failed ?? "—"} sub={t?.failed_pct != null ? `${t.failed_pct}%` : undefined} icon={<ExclamationTriangleIcon className="h-5 w-5" />} tone="red" />
          <StatTile label="Delayed (> SLA)" value={t?.delayed ?? "—"} sub={t?.delayed_pct != null ? `${t.delayed_pct}%` : undefined} icon={<ClockIcon className="h-5 w-5" />} tone="amber" />
        </div>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-5">
          <SectionCard className="xl:col-span-2" title="Application Health" icon={<MonitorIcon className="h-5 w-5" />}
            extra={<>
              {health && <Badge tone={STATUS_STYLE[health.status].tone}>{health.status === "ok" ? "All systems healthy" : STATUS_STYLE[health.status].label}</Badge>}
              {viewAll("/support/health")}
            </>}>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {(health?.components ?? []).map((c) => (
                <div key={c.key} className="flex items-center justify-between rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800" title={c.detail}>
                  <span className="flex items-center gap-2 text-sm text-slate-800 dark:text-slate-100">
                    <span className={`h-2.5 w-2.5 rounded-full ${STATUS_STYLE[c.status].dot}`} />{c.label}
                  </span>
                  <span className={`text-xs font-semibold ${c.status === "ok" ? "text-emerald-600" : c.status === "down" ? "text-rose-600" : c.status === "warn" ? "text-amber-600" : "text-slate-500"}`}>
                    {STATUS_STYLE[c.status].label}
                  </span>
                </div>
              ))}
            </div>
          </SectionCard>
          <SectionCard className="xl:col-span-3" title={<>Job Pipeline <span className="text-sm font-normal text-slate-500">(where this period's jobs are now)</span></>}
            icon={<LayersIcon className="h-5 w-5" />}>
            <div className="flex flex-wrap items-center gap-2">
              {(data?.pipeline ?? []).map((p, i, all) => (
                <div key={p.key} className="flex items-center gap-2">
                  <Link to={`/support/jobs?stage=${p.key}&period=${period}`}
                    className={`min-w-[6.5rem] rounded-xl px-3 py-2.5 text-center ring-1 ring-inset transition hover:-translate-y-0.5 hover:shadow ${PIPE_TONE[STAGE_TONE[p.key] ?? "slate"]}`}>
                    <p className="text-2xl font-bold tabular-nums">{p.count}</p>
                    <p className="text-xs font-medium">{p.label}</p>
                  </Link>
                  {i < all.length - 2 && <span className="text-slate-300 dark:text-slate-600">→</span>}
                </div>
              ))}
            </div>
          </SectionCard>
        </div>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
          <SectionCard title="Processing Performance" icon={<ClockIcon className="h-5 w-5" />} extra={viewAll("/support/performance")}>
            <ul className="space-y-2">
              {(perf?.stages ?? []).map((s) => (
                <li key={s.key} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm dark:bg-slate-800/60">
                  <span className="text-slate-700 dark:text-slate-200">{s.label}</span>
                  <span className="flex items-center gap-2">
                    <span className={`rounded-md px-2 py-0.5 font-semibold tabular-nums ${s.avg == null ? "text-slate-400" : s.ok ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300" : "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300"}`}>{mins(s.avg)}</span>
                    <span className="text-xs text-slate-500">≤ {s.sla} min</span>
                  </span>
                </li>
              ))}
            </ul>
          </SectionCard>
          <SectionCard title="Jobs per day" icon={<ChartBarIcon className="h-5 w-5" />}>
            {data && <VolumeBars data={data.volume} />}
          </SectionCard>
          <SectionCard title="Attention Required" icon={<ExclamationTriangleIcon className="h-5 w-5" />} extra={viewAll("/support/issues")}>
            <ul className="space-y-1.5">
              {(data?.attention ?? []).map((a) => (
                <li key={a.key} className="flex items-center justify-between rounded-lg px-2 py-1.5 text-sm hover:bg-slate-50 dark:hover:bg-slate-800/50">
                  <span className="text-slate-700 dark:text-slate-300">{a.label}</span>
                  {a.count > 0 ? <Badge tone={ATTENTION_TONE[a.key] ?? "red"} dot={false}>{a.count}</Badge>
                    : <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-semibold text-slate-500 dark:bg-slate-800">0</span>}
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-slate-500">Open now, all customers, jobs from the last {data?.attention_window_days ?? 60} days. Handled ones are hidden.</p>
          </SectionCard>
        </div>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
          <SectionCard className="xl:col-span-2" padded={false} title="Failed / Delayed Jobs" icon={<ExclamationTriangleIcon className="h-5 w-5" />} extra={viewAll("/support/issues")}>
            <IssuesTable data={issues} tab={tab} onTab={setTab} onChanged={load} limit={8} />
          </SectionCard>
          <SectionCard title="Recent Activity" icon={<CheckBadgeIcon className="h-5 w-5" />} extra={viewAll("/support/activity")}>
            <ActivityList items={data?.activity ?? []} />
          </SectionCard>
        </div>
      </div>
    </AppShell>
  );
}
