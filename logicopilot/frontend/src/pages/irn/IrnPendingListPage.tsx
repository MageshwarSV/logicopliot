import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import * as publicIrnApi from "../../api/publicIrn";
import type { Job } from "../../types/jobs";
import { DataTable, type Column } from "../../components/ui/DataTable";
import { PublicIrnShell } from "./PublicIrnShell";

/** A standalone list of every job GK2 has parked in "IRN Document Process" - reachable only
 *  by its own direct link, with a `key` query param (see app/api/v1/public_irn.py on the
 *  backend for what that key actually is), never linked from the normal job screens or
 *  sidebar navigation, and requiring NO login. Styled to look like the real app's Jobs list
 *  (same sidebar + table language, via PublicIrnShell/DataTable) without being wired into it. */
export function IrnPendingListPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const key = searchParams.get("key") ?? "";
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!key) {
      setError("This link is missing its access key.");
      return;
    }
    publicIrnApi
      .listIrnPending(key)
      .then(setJobs)
      .catch(() => setError("This link is invalid or has expired."));
  }, [key]);

  function when(iso?: string | null) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
  }

  function statusPill(status?: string | null) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
        {status ?? "IRN Document Process"}
      </span>
    );
  }

  const columns: Column<Job>[] = [
    { header: "Date", render: (j) => <span className="text-slate-600 dark:text-slate-300">{when(j.created_at)}</span> },
    { header: "Job No", render: (j) => <span className="font-mono font-medium text-slate-900 dark:text-slate-100">{j.reference}</span> },
    { header: "ETA", render: (j) => <span className="text-slate-600 dark:text-slate-300">{when(j.eta_date)}</span> },
    { header: "Status", render: (j) => statusPill(j.outer_status) },
    { header: "Importer / Exporter", render: (j) => <span className="text-slate-600 dark:text-slate-300">{j.customer_name ?? j.mode ?? "—"}</span> },
  ];

  return (
    <PublicIrnShell title="IRN Document Process" subtitle="Every job currently parked here, waiting on the IRN documents step.">
      {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
      {!error && jobs === null && <p className="text-sm text-slate-400">Loading…</p>}
      {!error && jobs !== null && (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <DataTable
            columns={columns}
            rows={jobs}
            keyFor={(j) => j.id}
            emptyMessage="No jobs are in IRN Document Process right now."
            onRowClick={(j) => navigate(`/irn-pending/${j.id}?key=${encodeURIComponent(key)}`)}
          />
        </div>
      )}
    </PublicIrnShell>
  );
}
