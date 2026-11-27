/** Production Support - Job Monitoring: every customer's jobs with stage and stage times. */
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import * as supportApi from "../../api/support";
import type { SupportJobs } from "../../api/support";
import { PERIODS, PeriodPicker, formatWhen, mins, rangeFor, type Period } from "./supportUi";

export function SupportJobsPage() {
  const [params] = useSearchParams();
  const initialPeriod = PERIODS.some((p) => p.key === params.get("period")) ? (params.get("period") as Period) : "7d";
  const [period, setPeriod] = useState<Period>(initialPeriod);
  const [stage, setStage] = useState(params.get("stage") ?? "");
  const [tenant, setTenant] = useState(params.get("customer") ?? "");
  const [q, setQ] = useState("");
  const [data, setData] = useState<SupportJobs | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [from, to] = rangeFor(period);
      setData(await supportApi.getJobs({ date_from: from, date_to: to, stage: stage || undefined, tenant_id: tenant || undefined, q: q || undefined }));
    } catch (err) {
      setError(axios.isAxiosError(err) ? (err.response?.data?.detail ?? "Could not load jobs.") : "Could not load jobs.");
    }
  }, [period, stage, tenant, q]);

  useEffect(() => {
    const t = window.setTimeout(load, 250);
    return () => window.clearTimeout(t);
  }, [load]);

  const field = "rounded-lg border border-slate-300 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900";
  return (
    <AppShell title="Job Monitoring" subtitle="Every customer's jobs - where each one is and how long each stage took.">
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
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Job no." className={field} />
        </div>
        {error && <Alert>{error}</Alert>}
        <Card className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
                <tr>
                  <th className="px-3 py-2">Job No.</th><th className="px-3 py-2">Client</th><th className="px-3 py-2">Type</th>
                  <th className="px-3 py-2">Stage</th><th className="px-3 py-2 text-right">Pages</th><th className="px-3 py-2">Arrived</th>
                  <th className="px-3 py-2 text-right">Email→AI</th><th className="px-3 py-2 text-right">Queue</th>
                  <th className="px-3 py-2 text-right">Extraction</th><th className="px-3 py-2 text-right">ERP</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {(data?.items ?? []).length === 0 && (
                  <tr><td colSpan={10} className="px-3 py-6 text-center text-slate-500">{data ? "No jobs match." : "Loading…"}</td></tr>
                )}
                {(data?.items ?? []).map((j) => (
                  <tr key={j.job_id}>
                    <td className="px-3 py-2 font-semibold">
                      <Link to={`/jobs/${j.job_id}`} className="text-indigo-700 hover:underline dark:text-indigo-300">{j.reference}</Link>
                      {j.delayed && <span className="ml-1 rounded bg-amber-100 px-1 text-[10px] font-semibold text-amber-800">SLA</span>}
                    </td>
                    <td className="px-3 py-2">{j.client}</td>
                    <td className="px-3 py-2">{j.type}</td>
                    <td className="px-3 py-2">{j.stage_label}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{j.pages}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{formatWhen(j.created_at)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{mins(j.timings.email_to_ai)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{mins(j.timings.queue_wait)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{mins(j.timings.extraction)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{mins(j.timings.erp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data && data.total > data.items.length && (
            <p className="px-3 py-2 text-xs text-slate-500">Showing {data.items.length} of {data.total} - narrow the filters to see the rest.</p>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
