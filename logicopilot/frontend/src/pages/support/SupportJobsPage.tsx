/** Production Support - Job Monitoring: every customer's jobs, where each one is and how long
 *  each stage took (red when over the SLA). Today by default; paged. */
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Alert } from "../../components/ui/Alert";
import { BriefcaseIcon, SearchIcon } from "../../components/ui/icons";
import * as supportApi from "../../api/support";
import type { SupportJobs } from "../../api/support";
import {
  DEFAULT_PERIOD, EmptyRow, PERIODS, Pager, PeriodPicker, ROW_EDGE, STAGE_TONE, SectionCard, StageBadge, T, TypeCell,
  formatWhen, mins, rangeFor, usePaged, type Period,
} from "./supportUi";

type StageKey = "email_to_ai" | "queue_wait" | "extraction" | "erp";

export function SupportJobsPage() {
  const [params] = useSearchParams();
  const initialPeriod = PERIODS.some((p) => p.key === params.get("period")) ? (params.get("period") as Period) : DEFAULT_PERIOD;
  const [period, setPeriod] = useState<Period>(initialPeriod);
  const [stage, setStage] = useState(params.get("stage") ?? "");
  const [tenant, setTenant] = useState(params.get("customer") ?? "");
  const [q, setQ] = useState("");
  const [data, setData] = useState<SupportJobs | null>(null);
  const [sla, setSla] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { supportApi.getSettings().then((s) => setSla(s.sla)).catch(() => undefined); }, []);

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const [from, to] = rangeFor(period);
      setData(await supportApi.getJobs({ date_from: from, date_to: to, stage: stage || undefined, tenant_id: tenant || undefined, q: q || undefined, limit: 1000 }));
    } catch (err) {
      setError(axios.isAxiosError(err) ? (err.response?.data?.detail ?? "Could not load jobs.") : "Could not load jobs.");
    } finally {
      setLoading(false);
    }
  }, [period, stage, tenant, q]);

  useEffect(() => {
    const t = window.setTimeout(load, 250);
    return () => window.clearTimeout(t);
  }, [load]);

  const paged = usePaged(data?.items ?? [], `${period}|${stage}|${tenant}|${q}`);

  const timeCell = (v: number | null, key: StageKey) => {
    const over = v != null && sla[key] != null && v > sla[key];
    return (
      <td className={`${T.tdRight} ${over ? "font-semibold text-rose-600 dark:text-rose-400" : v == null ? "text-slate-400" : "text-slate-700 dark:text-slate-200"}`}
        title={over ? `Over the SLA (${sla[key]} min)` : undefined}>
        {mins(v)}
      </td>
    );
  };

  const field = "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm dark:border-slate-700 dark:bg-slate-900";
  return (
    <AppShell title="Job Monitoring" icon={<BriefcaseIcon className="h-6 w-6" />}
      subtitle="Every customer's jobs - where each one is and how long each stage took.">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <PeriodPicker value={period} onChange={setPeriod} />
          <select value={tenant} onChange={(e) => setTenant(e.target.value)} className={field}>
            <option value="">All customers</option>
            {(data?.customers ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select value={stage} onChange={(e) => setStage(e.target.value)} className={field}>
            <option value="">All stages</option>
            {(data?.stages ?? []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <div className="relative">
            <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Job no." className={`${field} pl-8`} />
          </div>
        </div>
        {error && <Alert>{error}</Alert>}
        <SectionCard padded={false} title={<>Jobs <span className="text-sm font-normal text-slate-500">({data?.total ?? 0})</span></>}
          extra={<span className="text-xs text-slate-500">Times in red are over the SLA</span>}>
          <div className={T.wrap}>
            <table className={T.table}>
              <thead className={T.thead}>
                <tr>
                  <th className={T.thSno}>S.No</th><th className={T.th}>Job No.</th><th className={T.th}>Client</th><th className={T.th}>Type</th>
                  <th className={T.th}>Stage</th><th className={T.thRight}>Pages</th><th className={T.th}>Arrived</th>
                  <th className={T.thRight}>Email → AI</th><th className={T.thRight}>Queue</th>
                  <th className={T.thRight}>Extraction</th><th className={T.thRight}>ERP</th>
                </tr>
              </thead>
              <tbody>
                {paged.shown.length === 0 && <EmptyRow cols={11} loading={loading && !data} text="No jobs match these filters." />}
                {paged.shown.map((j, i) => (
                  <tr key={j.job_id} className={`${T.row} ${ROW_EDGE[STAGE_TONE[j.stage] ?? "slate"]} ${j.stage === "failed" ? "bg-rose-50/40 dark:bg-rose-500/5" : ""}`}>
                    <td className={T.tdSno}>{(paged.page - 1) * paged.size + i + 1}</td>
                    <td className={`${T.td} whitespace-nowrap`}>
                      <Link to={`/jobs/${j.job_id}`} className={T.link}>{j.reference}</Link>
                      {j.delayed && <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold uppercase text-amber-800 dark:bg-amber-500/20 dark:text-amber-300">SLA</span>}
                    </td>
                    <td className={T.td}>{j.client}</td>
                    <td className={T.td}><TypeCell type={j.type} /></td>
                    <td className={T.td}><StageBadge stage={j.stage} label={j.stage_label} /></td>
                    <td className={T.tdRight}>{j.pages}</td>
                    <td className={`${T.td} whitespace-nowrap text-slate-600 dark:text-slate-300`}>{formatWhen(j.created_at)}</td>
                    {timeCell(j.timings.email_to_ai, "email_to_ai")}
                    {timeCell(j.timings.queue_wait, "queue_wait")}
                    {timeCell(j.timings.extraction, "extraction")}
                    {timeCell(j.timings.erp, "erp")}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager {...paged} />
          {data && data.total > data.items.length && (
            <p className="px-4 pb-3 text-xs text-slate-500">The newest {data.items.length} of {data.total} are listed - narrow the filters to see older ones.</p>
          )}
        </SectionCard>
      </div>
    </AppShell>
  );
}
