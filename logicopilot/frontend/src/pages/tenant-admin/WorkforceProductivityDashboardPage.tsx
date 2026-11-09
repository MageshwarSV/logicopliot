import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card, StatCard } from "../../components/ui/Card";
import { UsersIcon, ClockIcon, DocumentIcon } from "../../components/ui/icons";
import { DashboardFilterBar, defaultDashboardRange, localIso, type DashboardRange } from "../../components/tenant-admin/DashboardFilterBar";
import { useAuth } from "../../auth/useAuth";
import * as tenantsApi from "../../api/tenants";
import type { WorkforceDashboard } from "../../api/tenants";

function fmtMinutes(m: number | null): string {
  if (m == null) return "—";
  if (m < 60) return `${Math.round(m * 10) / 10} mins`;
  const h = Math.floor(m / 60);
  const rem = Math.round(m % 60);
  return `${h}h ${rem}m`;
}

/** Workforce Productivity detail page of the Executive Dashboard. Only covers jobs with a
 *  real recorded GK1/GK2 "Approved and Proceed" timestamp (Job.validation_approved_at/
 *  gk2_validation_approved_at, added specifically for this page - see
 *  app/core/dashboard_metrics.py's compute_workforce_metrics) - a job that sailed through
 *  Data Validation with no explicit approval contributes nothing, which the coverage note
 *  below makes visible rather than silently averaging over fewer jobs than it looks like. */
export function WorkforceProductivityDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [range, setRange] = useState<DashboardRange>(defaultDashboardRange());
  const [data, setData] = useState<WorkforceDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user?.tenant_id) return;
    setLoading(true);
    setError(null);
    tenantsApi
      .getWorkforceDashboard(user.tenant_id, { dateFrom: range.dateFrom, dateTo: range.dateTo, mode: range.mode })
      .then(setData)
      .catch(() => setError("Could not load this report."))
      .finally(() => setLoading(false));
  }, [user?.tenant_id, range.dateFrom, range.dateTo, range.mode]);

  const today = localIso(new Date());

  return (
    <AppShell
      title="Workforce Productivity"
      subtitle="Human review effort, productivity and resource requirement, for jobs completed in this range."
    >
      <DashboardFilterBar value={range} onChange={setRange} bounds={{ min: "2020-01-01", max: today }} />

      {error && <p className="mb-4 text-sm text-rose-600 dark:text-rose-400">{error}</p>}

      <div className="mb-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Employee Requirement (FTE)"
          value={loading ? "…" : data?.employee_requirement_fte != null ? `${data.employee_requirement_fte} FTE` : "—"}
          icon={<UsersIcon />}
          tone="violet"
        />
        <StatCard
          label="Avg. Review Time per Job"
          value={loading ? "…" : fmtMinutes(data?.avg_review_minutes_per_job ?? null)}
          icon={<ClockIcon />}
          tone="amber"
        />
        <StatCard
          label="Total Jobs"
          value={loading ? "…" : (data?.total_jobs ?? 0).toLocaleString()}
          icon={<DocumentIcon />}
          tone="indigo"
        />
        <StatCard
          label="Human Review Hours"
          value={loading ? "…" : `${(data?.human_review_hours ?? 0).toLocaleString()} hrs`}
          icon={<ClockIcon />}
          tone="blue"
        />
      </div>

      {!loading && data && data.total_jobs > 0 && (
        <p className="mb-6 text-xs text-slate-400">
          {data.jobs_with_recorded_review} of {data.total_jobs} completed job{data.total_jobs === 1 ? "" : "s"} in this range had
          a recorded review approval to measure — the rest either skipped straight through (no cross-document checks to
          confirm) or finished before this tracking existed.
        </p>
      )}

      <Card className="p-5">
        <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Employee Effort Summary</h2>
        {loading ? (
          <p className="py-8 text-center text-sm text-slate-400">Loading…</p>
        ) : !data || data.by_employee.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-400">No recorded reviews in this range.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                <th className="pb-2 font-medium">Employee</th>
                <th className="pb-2 font-medium">Jobs Reviewed</th>
                <th className="pb-2 font-medium">Review Hours</th>
                <th className="pb-2 font-medium">Avg. Time/Job</th>
              </tr>
            </thead>
            <tbody>
              {data.by_employee.map((row) => (
                <tr key={row.user_id} className="border-b border-slate-50 last:border-0 dark:border-slate-800/60">
                  <td className="py-2 text-slate-700 dark:text-slate-200">{row.name}</td>
                  <td className="py-2 text-slate-500 dark:text-slate-400">{row.jobs_reviewed.toLocaleString()}</td>
                  <td className="py-2 text-slate-500 dark:text-slate-400">{row.review_hours.toLocaleString()} hrs</td>
                  <td className="py-2 text-slate-500 dark:text-slate-400">{fmtMinutes(row.avg_minutes_per_job)}</td>
                </tr>
              ))}
              <tr className="font-semibold text-slate-900 dark:text-slate-50">
                <td className="pt-2">Total</td>
                <td className="pt-2">{data.by_employee.reduce((s, r) => s + r.jobs_reviewed, 0).toLocaleString()}</td>
                <td className="pt-2">{data.human_review_hours.toLocaleString()} hrs</td>
                <td className="pt-2">{fmtMinutes(data.avg_review_minutes_per_job)}</td>
              </tr>
            </tbody>
          </table>
        )}
      </Card>

      {!loading && data && (
        <Card className="mt-4 p-4 text-xs text-slate-500 dark:text-slate-400">
          FTE calculation: {data.human_review_hours.toLocaleString()} hrs ÷ working hours in this range (5 hrs/day × 6
          days/week) = {data.employee_requirement_fte ?? "—"} FTE.
        </Card>
      )}

      <button
        type="button"
        onClick={() => navigate("/tenant-admin")}
        className="mt-6 text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400"
      >
        ← Back to Overview
      </button>
    </AppShell>
  );
}
