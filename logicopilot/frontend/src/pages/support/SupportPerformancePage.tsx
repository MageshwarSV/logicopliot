/** Production Support - Processing Performance: time per stage against the SLA, and the jobs
 *  that went over it. Today by default. */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Alert } from "../../components/ui/Alert";
import { CheckCircleIcon, ClockIcon, ExclamationTriangleIcon } from "../../components/ui/icons";
import * as supportApi from "../../api/support";
import type { SupportPerformance } from "../../api/support";
import { DEFAULT_PERIOD, EmptyRow, Pager, PeriodPicker, ROW_EDGE, SectionCard, T, formatWhen, mins, rangeFor, usePaged, type Period } from "./supportUi";

export function SupportPerformancePage() {
  const [period, setPeriod] = useState<Period>(DEFAULT_PERIOD);
  const [data, setData] = useState<SupportPerformance | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [from, to] = rangeFor(period);
      setData(await supportApi.getPerformance(from, to));
    } catch (err) {
      setError(axios.isAxiosError(err) ? (err.response?.data?.detail ?? "Could not load.") : "Could not load.");
    }
  }, [period]);

  useEffect(() => { load(); }, [load]);
  const paged = usePaged(data?.delayed ?? [], period);

  return (
    <AppShell title="Processing Performance" icon={<ClockIcon className="h-6 w-6" />}
      subtitle="How long each stage takes, against the agreed SLA.">
      <div className="space-y-4">
        <PeriodPicker value={period} onChange={setPeriod} />
        {error && <Alert>{error}</Alert>}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {(data?.stages ?? []).map((s) => {
            const ok = s.avg == null || s.ok;
            return (
              <div key={s.key} className={`rounded-2xl border bg-white p-4 shadow-sm dark:bg-slate-900 ${ok ? "border-slate-200 dark:border-slate-800" : "border-amber-300 dark:border-amber-500/40"}`}>
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{s.label}</p>
                  {s.avg != null && (ok
                    ? <CheckCircleIcon className="h-5 w-5 text-emerald-500" />
                    : <ExclamationTriangleIcon className="h-5 w-5 text-amber-500" />)}
                </div>
                <p className={`mt-1 text-2xl font-bold tabular-nums ${s.avg == null ? "text-slate-400" : ok ? "text-slate-900 dark:text-slate-50" : "text-amber-700 dark:text-amber-300"}`}>{mins(s.avg)}</p>
                <p className="text-xs text-slate-500">SLA ≤ {s.sla} min · slowest 10% {mins(s.p90)}</p>
                <p className={`mt-1 text-xs font-medium ${s.over_sla ? "text-rose-600 dark:text-rose-400" : "text-slate-500"}`}>
                  {s.over_sla} of {s.measured} over SLA
                </p>
              </div>
            );
          })}
        </div>
        <SectionCard padded={false} title={<>Delayed jobs <span className="text-sm font-normal text-slate-500">({data?.delayed_count ?? 0} of {data?.jobs ?? 0})</span></>}
          extra={<span className="text-xs text-slate-500">{data?.note}</span>}>
          <div className={T.wrap}>
            <table className={T.table}>
              <thead className={T.thead}><tr><th className={T.th}>Job</th><th className={T.th}>Client</th><th className={T.th}>Arrived</th><th className={T.th}>Over the SLA</th></tr></thead>
              <tbody>
                {paged.shown.length === 0 && <EmptyRow cols={4} loading={!data} text="✓ No job went over the SLA in this period." />}
                {paged.shown.map((d) => (
                  <tr key={d.job_id} className={`${T.row} ${ROW_EDGE.orange}`}>
                    <td className={T.td}><Link to={`/jobs/${d.job_id}`} className={T.link}>{d.reference}</Link></td>
                    <td className={T.td}>{d.client}</td>
                    <td className={`${T.td} whitespace-nowrap`}>{formatWhen(d.created_at)}</td>
                    <td className={T.td}>
                      <div className="flex flex-wrap gap-1.5">
                        {d.stages.map((s) => (
                          <span key={s.stage} className="rounded-md bg-orange-50 px-2 py-0.5 text-xs font-medium text-orange-800 dark:bg-orange-500/15 dark:text-orange-300">
                            {s.stage}: <b>{mins(s.minutes)}</b> (SLA {s.sla} min)
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager {...paged} />
        </SectionCard>
      </div>
    </AppShell>
  );
}
