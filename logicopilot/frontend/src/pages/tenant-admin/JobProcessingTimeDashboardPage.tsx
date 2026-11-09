import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card, StatCard } from "../../components/ui/Card";
import { ClockIcon, DocumentIcon, ArrowRightIcon } from "../../components/ui/icons";
import { LineChart } from "../../components/charts/LineChart";
import { DashboardFilterBar, defaultDashboardRange, type DashboardRange } from "../../components/tenant-admin/DashboardFilterBar";
import { useAuth } from "../../auth/useAuth";
import * as tenantsApi from "../../api/tenants";
import type { ProcessingTimeDashboard } from "../../api/tenants";

function fmtDay(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
}

function fmtMinutes(m: number | null): string {
  if (m == null) return "—";
  if (m < 60) return `${Math.round(m * 10) / 10} mins`;
  const h = Math.floor(m / 60);
  const rem = Math.round(m % 60);
  return `${h}h ${rem}m`;
}

/** Job Processing Time detail page of the Executive Dashboard - see the mockup's own "Job
 *  Process Flow & Average Time" screen. Extraction/Review/ERP Submission durations are
 *  reconstructed from JobEvent history and Job.ai_submitted_at/completed_at (no new tracking
 *  needed) - see app/core/dashboard_metrics.py's own docstring for exactly how. */
export function JobProcessingTimeDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [range, setRange] = useState<DashboardRange>(defaultDashboardRange());
  const [data, setData] = useState<ProcessingTimeDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user?.tenant_id) return;
    setLoading(true);
    setError(null);
    tenantsApi
      .getProcessingTimeDashboard(user.tenant_id, { dateFrom: range.dateFrom, dateTo: range.dateTo, mode: range.mode })
      .then(setData)
      .catch(() => setError("Could not load this report."))
      .finally(() => setLoading(false));
  }, [user?.tenant_id, range.dateFrom, range.dateTo, range.mode]);

  const today = new Date().toISOString().slice(0, 10);
  const steps = [
    { label: "Pre-Alert Received" },
    { label: "AI Extraction", minutes: data?.avg_extraction_minutes ?? null },
    { label: "Review", minutes: data?.avg_review_minutes ?? null },
    { label: "ERP Submission", minutes: data?.avg_erp_submission_minutes ?? null },
    { label: "ERP Submitted" },
  ];

  return (
    <AppShell
      title="Job Processing Time"
      subtitle="End-to-end job processing time (Pre-Alert to ERP Submission), for jobs completed in this range."
    >
      <DashboardFilterBar value={range} onChange={setRange} bounds={{ min: "2020-01-01", max: today }} />

      {error && <p className="mb-4 text-sm text-rose-600 dark:text-rose-400">{error}</p>}

      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard
          label="Avg. Total TAT (End-to-End)"
          value={loading ? "…" : fmtMinutes(data?.avg_total_tat_minutes ?? null)}
          icon={<ClockIcon />}
          tone="blue"
        />
        <StatCard
          label="Total Jobs"
          value={loading ? "…" : (data?.total_jobs ?? 0).toLocaleString()}
          icon={<DocumentIcon />}
          tone="indigo"
        />
        <StatCard
          label="Avg. Extraction Time"
          value={loading ? "…" : fmtMinutes(data?.avg_extraction_minutes ?? null)}
          tone="violet"
        />
        <StatCard
          label="Avg. Review Time"
          value={loading ? "…" : fmtMinutes(data?.avg_review_minutes ?? null)}
          tone="amber"
        />
        <StatCard
          label="Avg. ERP Submission Time"
          value={loading ? "…" : fmtMinutes(data?.avg_erp_submission_minutes ?? null)}
          tone="emerald"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="mb-5 font-semibold text-slate-900 dark:text-slate-50">Job Process Flow &amp; Average Time</h2>
          <div className="flex flex-wrap items-center gap-2">
            {steps.map((s, i) => (
              <div key={s.label} className="flex items-center gap-2">
                <div className="flex min-w-[108px] flex-col items-center gap-1 rounded-xl border border-slate-200 px-3 py-3 text-center dark:border-slate-700">
                  <span className="text-xs font-medium text-slate-600 dark:text-slate-300">{s.label}</span>
                  {"minutes" in s && (
                    <span className="text-xs text-slate-400">{fmtMinutes(s.minutes ?? null)}</span>
                  )}
                </div>
                {i < steps.length - 1 && <ArrowRightIcon className="h-4 w-4 shrink-0 text-slate-300 dark:text-slate-600" />}
              </div>
            ))}
          </div>
          <p className="mt-5 text-sm text-slate-500 dark:text-slate-400">
            Total Avg. TAT per Job:{" "}
            <span className="font-semibold text-slate-900 dark:text-slate-50">
              {loading ? "…" : fmtMinutes(data?.avg_total_tat_minutes ?? null)}
            </span>
          </p>
          {!loading && data && data.avg_erp_submission_minutes == null && (
            <p className="mt-2 text-xs text-slate-400">
              Some completed jobs went straight through Submit Entry with no separate AI/ERP hand-off step, so an
              ERP Submission average could not be computed for every job.
            </p>
          )}
        </Card>

        <Card className="p-5">
          <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">TAT Trend (Avg. Total Time per Job)</h2>
          {loading ? (
            <p className="py-8 text-center text-sm text-slate-400">Loading…</p>
          ) : (
            <LineChart
              points={(data?.trend ?? []).map((p) => ({ label: fmtDay(p.day), value: p.avg_total_tat_minutes ?? null }))}
              color="#2563eb"
              valueFormatter={(v) => `${Math.round(v * 10) / 10}m`}
            />
          )}
        </Card>
      </div>

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
