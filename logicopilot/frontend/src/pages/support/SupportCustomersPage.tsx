/** Production Support - Customer View: per customer, this period's jobs and what is open now. */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Alert } from "../../components/ui/Alert";
import { BuildingIcon } from "../../components/ui/icons";
import * as supportApi from "../../api/support";
import type { CustomerRow } from "../../api/support";
import { Badge, DEFAULT_PERIOD, EmptyRow, Pager, PeriodPicker, ROW_EDGE, SectionCard, T, formatWhen, rangeFor, usePaged, type Period } from "./supportUi";

export function SupportCustomersPage() {
  const [period, setPeriod] = useState<Period>(DEFAULT_PERIOD);
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
  const paged = usePaged(rows ?? [], period);

  return (
    <AppShell title="Customer View" icon={<BuildingIcon className="h-6 w-6" />}
      subtitle="Each customer's jobs this period, and what is open for them now.">
      <div className="space-y-4">
        <PeriodPicker value={period} onChange={setPeriod} />
        {error && <Alert>{error}</Alert>}
        <SectionCard padded={false} title={<>Customers <span className="text-sm font-normal text-slate-500">({rows?.length ?? 0})</span></>}>
          <div className={T.wrap}>
            <table className={T.table}>
              <thead className={T.thead}>
                <tr>
                  <th className={T.thSno}>S.No</th><th className={T.th}>Customer</th><th className={T.thRight}>Jobs</th><th className={T.thRight}>Submitted</th>
                  <th className={T.thRight}>Failed</th><th className={T.thRight}>In progress</th>
                  <th className={T.th}>Open problems</th><th className={T.th}>Last job</th><th className={T.th} />
                </tr>
              </thead>
              <tbody>
                {paged.shown.length === 0 && <EmptyRow cols={9} loading={!rows} text="No customers with jobs." />}
                {paged.shown.map((r, i) => (
                  <tr key={r.tenant_id} className={`${T.row} ${ROW_EDGE[r.open_issues ? "red" : r.jobs ? "green" : "slate"]}`}>
                    <td className={T.tdSno}>{(paged.page - 1) * paged.size + i + 1}</td>
                    <td className={`${T.td} font-semibold text-slate-900 dark:text-slate-50`}>{r.name}</td>
                    <td className={T.tdRight}>{r.jobs}</td>
                    <td className={`${T.tdRight} text-emerald-700 dark:text-emerald-300`}>{r.submitted}</td>
                    <td className={`${T.tdRight} ${r.failed ? "font-semibold text-rose-600 dark:text-rose-400" : ""}`}>{r.failed}</td>
                    <td className={`${T.tdRight} text-sky-700 dark:text-sky-300`}>{r.in_progress}</td>
                    <td className={T.td}>{r.open_issues ? <Badge tone="red">{r.open_issues} open</Badge> : <Badge tone="green">None</Badge>}</td>
                    <td className={`${T.td} whitespace-nowrap`}>{formatWhen(r.last_job_at)}</td>
                    <td className={`${T.td} text-right`}>
                      <Link to={`/support/jobs?customer=${r.tenant_id}&period=${period}`} className="text-xs font-semibold text-indigo-700 hover:underline dark:text-indigo-300">View jobs →</Link>
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
