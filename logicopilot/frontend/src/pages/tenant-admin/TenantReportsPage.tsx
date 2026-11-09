import { useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import { JobActivityChart } from "../../components/JobActivityChart";
import { useAuth } from "../../auth/useAuth";
import * as tenantsApi from "../../api/tenants";
import type { TenantReportType } from "../../api/tenants";

// "job-activity" is a client-side-only tab - it renders the live chart, not a
// getTenantReport() row set, so it has no backend report_type of its own and no CSV/Excel
// export (the chart has nothing to export; it's a live view, not a point-in-time pull).
type TabValue = TenantReportType | "job-activity";

const REPORTS: { type: TabValue; label: string }[] = [
  { type: "job-activity", label: "Job Activity" },
  { type: "monthly-usage", label: "Monthly Usage" },
  { type: "billing-summary", label: "Billing Summary" },
  { type: "revenue-trend", label: "Revenue Trend" },
  { type: "outstanding-payments", label: "Outstanding Payments" },
];

/** A tenant-scoped version of SaaS Finance's own Reports page (see
 *  pages/saas-finance/ReportsPage.tsx) - same report-tab/table/export shape, trailing-12-month
 *  trend data scoped to just this tenant instead of every customer on the platform. Job
 *  Activity reuses the exact same chart the Operator dashboard already shows (JobActivityChart
 *  calls /jobs/activity-chart, already tenant-scoped and already allows TENANT_ADMIN - no
 *  backend change needed to show it here too). */
export function TenantReportsPage() {
  const { user } = useAuth();
  const [active, setActive] = useState<TabValue>("job-activity");
  const [rows, setRows] = useState<Record<string, string | number>[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<"csv" | "xlsx" | null>(null);

  const isJobActivity = active === "job-activity";

  useEffect(() => {
    if (!user?.tenant_id || isJobActivity) return;
    setLoading(true);
    setError(null);
    tenantsApi
      .getTenantReport(user.tenant_id, active)
      .then(setRows)
      .catch(() => setError("Could not load this report."))
      .finally(() => setLoading(false));
  }, [user?.tenant_id, active, isJobActivity]);

  async function handleExport(format: "csv" | "xlsx") {
    if (!user?.tenant_id || isJobActivity) return;
    setExporting(format);
    try {
      await tenantsApi.exportTenantReport(user.tenant_id, active, format);
    } catch {
      setError("Could not export this report.");
    } finally {
      setExporting(null);
    }
  }

  const headers = rows[0] ? Object.keys(rows[0]) : [];

  return (
    <AppShell title="Reports" subtitle="Your organization's own usage, billing and payment history.">
      <Card className="mb-4 flex flex-wrap items-center gap-2 p-4">
        {REPORTS.map((r) => (
          <button
            key={r.type}
            type="button"
            onClick={() => setActive(r.type)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              active === r.type
                ? "bg-indigo-600 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
            }`}
          >
            {r.label}
          </button>
        ))}
      </Card>

      {error && <div className="mb-4"><Alert>{error}</Alert></div>}

      {isJobActivity ? (
        <JobActivityChart />
      ) : (
        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold text-slate-900 dark:text-slate-50">
              {REPORTS.find((r) => r.type === active)?.label}
            </h2>
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" isLoading={exporting === "csv"} onClick={() => handleExport("csv")}>
                Export CSV
              </Button>
              <Button variant="secondary" size="sm" isLoading={exporting === "xlsx"} onClick={() => handleExport("xlsx")}>
                Export Excel
              </Button>
            </div>
          </div>

          {loading ? (
            <p className="py-10 text-center text-sm text-slate-400">Loading…</p>
          ) : rows.length === 0 ? (
            <p className="py-10 text-center text-sm text-slate-400">No data for this report.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                    {headers.map((h) => (
                      <th key={h} className="whitespace-nowrap py-2 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, i) => (
                    <tr key={i} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60">
                      {headers.map((h) => (
                        <td key={h} className="whitespace-nowrap py-2 text-slate-700 dark:text-slate-300">
                          {typeof row[h] === "number" ? (row[h] as number).toLocaleString() : row[h]}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </AppShell>
  );
}
