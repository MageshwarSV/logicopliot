/** Production Support - Processing Performance (S3): time per stage against the SLA, and the
 *  jobs that went over it. */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import * as supportApi from "../../api/support";
import type { SupportPerformance } from "../../api/support";
import { PeriodPicker, formatWhen, mins, rangeFor, type Period } from "./supportUi";

export function SupportPerformancePage() {
  const [period, setPeriod] = useState<Period>("7d");
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

  return (
    <AppShell title="Processing Performance" subtitle="How long each stage takes, against the agreed SLA.">
      <div className="space-y-5">
        <PeriodPicker value={period} onChange={setPeriod} />
        {error && <Alert>{error}</Alert>}
        <Card className="p-5">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr><th className="py-2">Stage</th><th className="text-right">Average</th><th className="text-right">Slowest 10%</th><th className="text-right">SLA</th><th className="text-right">Over SLA</th><th className="text-right">Jobs measured</th></tr>
            </thead>
            <tbody>
              {(data?.stages ?? []).map((s) => (
                <tr key={s.key} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="py-2 font-medium">{s.label}</td>
                  <td className={`text-right font-semibold tabular-nums ${s.avg == null ? "text-slate-400" : s.ok ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}`}>{mins(s.avg)}</td>
                  <td className="text-right tabular-nums">{mins(s.p90)}</td>
                  <td className="text-right tabular-nums">≤ {s.sla} min</td>
                  <td className={`text-right tabular-nums ${s.over_sla ? "text-rose-700 dark:text-rose-300" : ""}`}>{s.over_sla}</td>
                  <td className="text-right tabular-nums">{s.measured}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-slate-500">{data?.note} SLA minutes can be changed on Application Health.</p>
        </Card>
        <Card className="p-5">
          <h2 className="mb-3 font-semibold text-slate-900 dark:text-slate-50">Delayed jobs ({data?.delayed_count ?? 0} of {data?.jobs ?? 0})</h2>
          {(data?.delayed ?? []).length === 0 ? (
            <p className="text-sm text-slate-500">No job went over the SLA in this period.</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-slate-500"><tr><th className="py-2">Job</th><th>Client</th><th>Arrived</th><th>Over SLA</th></tr></thead>
              <tbody>
                {data!.delayed.map((d) => (
                  <tr key={d.job_id} className="border-t border-slate-100 align-top dark:border-slate-800">
                    <td className="py-2 font-semibold"><Link to={`/jobs/${d.job_id}`} className="text-indigo-700 hover:underline dark:text-indigo-300">{d.reference}</Link></td>
                    <td>{d.client}</td>
                    <td className="whitespace-nowrap">{formatWhen(d.created_at)}</td>
                    <td>{d.stages.map((s) => `${s.stage}: ${mins(s.minutes)} (SLA ${s.sla} min)`).join(" · ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
