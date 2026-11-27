/** Production Support - Customer View: per customer, this period's jobs and what is open now. */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import * as supportApi from "../../api/support";
import type { CustomerRow } from "../../api/support";
import { PeriodPicker, formatWhen, rangeFor, type Period } from "./supportUi";

export function SupportCustomersPage() {
  const [period, setPeriod] = useState<Period>("7d");
  const [rows, setRows] = useState<CustomerRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [from, to] = rangeFor(period);
      setRows(await supportApi.getCustomers(from, to));
    } catch (err) {
      setError(axios.isAxiosError(err) ? (err.response?.data?.detail ?? "Could not load.") : "Could not load.");
    }
  }, [period]);

  useEffect(() => { load(); }, [load]);

  return (
    <AppShell title="Customer View" subtitle="Each customer's jobs this period, and what is open for them now.">
      <div className="space-y-4">
        <PeriodPicker value={period} onChange={setPeriod} />
        {error && <Alert>{error}</Alert>}
        <Card className="p-0">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
              <tr>
                <th className="px-3 py-2">Customer</th><th className="px-3 py-2 text-right">Jobs</th><th className="px-3 py-2 text-right">Submitted</th>
                <th className="px-3 py-2 text-right">Failed</th><th className="px-3 py-2 text-right">In progress</th>
                <th className="px-3 py-2 text-right">Open problems</th><th className="px-3 py-2">Last job</th><th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {(rows ?? []).length === 0 && (
                <tr><td colSpan={8} className="px-3 py-6 text-center text-slate-500">{rows ? "No customers with jobs." : "Loading…"}</td></tr>
              )}
              {(rows ?? []).map((r) => (
                <tr key={r.tenant_id}>
                  <td className="px-3 py-2 font-medium">{r.name}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.jobs}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-emerald-700 dark:text-emerald-300">{r.submitted}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${r.failed ? "text-rose-700 dark:text-rose-300" : ""}`}>{r.failed}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.in_progress}</td>
                  <td className="px-3 py-2 text-right">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${r.open_issues ? "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300" : "bg-slate-100 text-slate-500 dark:bg-slate-800"}`}>{r.open_issues}</span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">{formatWhen(r.last_job_at)}</td>
                  <td className="px-3 py-2 text-right">
                    <Link to={`/support/jobs?customer=${r.tenant_id}&period=${period}`} className="text-xs font-semibold text-indigo-700 dark:text-indigo-300">Jobs →</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </AppShell>
  );
}
