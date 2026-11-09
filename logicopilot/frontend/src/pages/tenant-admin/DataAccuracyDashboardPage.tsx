import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card, StatCard } from "../../components/ui/Card";
import { DocumentIcon, CheckCircleIcon, ExclamationTriangleIcon, TargetIcon } from "../../components/ui/icons";
import { LineChart } from "../../components/charts/LineChart";
import { DashboardFilterBar, defaultDashboardRange, type DashboardRange } from "../../components/tenant-admin/DashboardFilterBar";
import { useAuth } from "../../auth/useAuth";
import * as tenantsApi from "../../api/tenants";
import type { AccuracyDashboard } from "../../api/tenants";

function fmtDay(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
}

/** Data Accuracy detail page of the Executive Dashboard - see the "CARGORA - Executive
 *  Dashboard" mockup's own Data Accuracy screen. Numbers are real (see
 *  app/core/dashboard_metrics.py), scoped to jobs that finished within the selected range. */
export function DataAccuracyDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [range, setRange] = useState<DashboardRange>(defaultDashboardRange());
  const [data, setData] = useState<AccuracyDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user?.tenant_id) return;
    setLoading(true);
    setError(null);
    tenantsApi
      .getAccuracyDashboard(user.tenant_id, { dateFrom: range.dateFrom, dateTo: range.dateTo, mode: range.mode })
      .then(setData)
      .catch(() => setError("Could not load this report."))
      .finally(() => setLoading(false));
  }, [user?.tenant_id, range.dateFrom, range.dateTo, range.mode]);

  const today = new Date().toISOString().slice(0, 10);

  return (
    <AppShell
      title="Data Accuracy"
      subtitle="Document extraction accuracy and manual corrections, for jobs completed in this range."
    >
      <DashboardFilterBar value={range} onChange={setRange} bounds={{ min: "2020-01-01", max: today }} />

      {error && <p className="mb-4 text-sm text-rose-600 dark:text-rose-400">{error}</p>}

      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="AI Data Accuracy"
          value={data?.accuracy_pct != null ? `${data.accuracy_pct}%` : "—"}
          icon={<TargetIcon />}
          tone="emerald"
        />
        <StatCard
          label="Documents Processed"
          value={loading ? "…" : (data?.documents_processed ?? 0).toLocaleString()}
          icon={<DocumentIcon />}
          tone="blue"
        />
        <StatCard
          label="Fields Extracted by AI"
          value={loading ? "…" : (data?.fields_extracted_by_ai ?? 0).toLocaleString()}
          icon={<CheckCircleIcon />}
          tone="indigo"
        />
        <StatCard
          label="Fields Manually Edited"
          value={loading ? "…" : (data?.fields_manually_edited ?? 0).toLocaleString()}
          icon={<ExclamationTriangleIcon />}
          tone="amber"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Accuracy by Document Type</h2>
          {loading ? (
            <p className="py-8 text-center text-sm text-slate-400">Loading…</p>
          ) : !data || data.by_document_type.length === 0 ? (
            <p className="py-8 text-center text-sm text-slate-400">No document-read fields in this range.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                  <th className="pb-2 font-medium">Document Type</th>
                  <th className="pb-2 font-medium">Documents</th>
                  <th className="pb-2 font-medium">AI Accuracy</th>
                </tr>
              </thead>
              <tbody>
                {data.by_document_type.map((row) => (
                  <tr key={row.document_name} className="border-b border-slate-50 last:border-0 dark:border-slate-800/60">
                    <td className="py-2 text-slate-700 dark:text-slate-200">{row.document_name}</td>
                    <td className="py-2 text-slate-500 dark:text-slate-400">{row.documents.toLocaleString()}</td>
                    <td className="py-2">
                      <span
                        className={
                          row.accuracy_pct == null
                            ? "text-slate-400"
                            : row.accuracy_pct >= 85
                              ? "font-medium text-emerald-600 dark:text-emerald-400"
                              : row.accuracy_pct >= 75
                                ? "font-medium text-amber-600 dark:text-amber-400"
                                : "font-medium text-rose-600 dark:text-rose-400"
                        }
                      >
                        {row.accuracy_pct != null ? `${row.accuracy_pct}%` : "—"}
                      </span>
                    </td>
                  </tr>
                ))}
                <tr className="font-semibold text-slate-900 dark:text-slate-50">
                  <td className="pt-2">Total</td>
                  <td className="pt-2">{data.by_document_type.reduce((s, r) => s + r.documents, 0).toLocaleString()}</td>
                  <td className="pt-2">{data.accuracy_pct != null ? `${data.accuracy_pct}%` : "—"}</td>
                </tr>
              </tbody>
            </table>
          )}
        </Card>

        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold text-slate-900 dark:text-slate-50">Accuracy Trend</h2>
            {data?.accuracy_pct != null && (
              <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
                {data.accuracy_pct}%
              </span>
            )}
          </div>
          {loading ? (
            <p className="py-8 text-center text-sm text-slate-400">Loading…</p>
          ) : (
            <LineChart
              points={(data?.trend ?? []).map((p) => ({ label: fmtDay(p.day), value: p.accuracy_pct ?? null }))}
              color="#4f46e5"
              valueSuffix="%"
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
