import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { JobsOverview } from "../../components/JobsOverview";
import { AppShell } from "../../components/AppShell";
import { Card, StatCard } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { ModeIcon } from "../../components/ModeIcon";
import { TargetIcon, UsersIcon, ClockIcon, ArrowRightIcon } from "../../components/ui/icons";
import { DashboardFilterBar, defaultDashboardRange, localIso, type DashboardRange } from "../../components/tenant-admin/DashboardFilterBar";
import { useAuth } from "../../auth/useAuth";
import * as usersApi from "../../api/users";
import * as jobsApi from "../../api/jobs";
import * as tenantsApi from "../../api/tenants";
import type { BillingUsage, AccuracyDashboard, ProcessingTimeDashboard, WorkforceDashboard } from "../../api/tenants";
import type { User } from "../../types/auth";
import type { Job } from "../../types/jobs";

const ALL = "All";

function formatInr(n: number): string {
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function fmtMinutes(m: number | null | undefined): string {
  if (m == null) return "—";
  if (m < 60) return `${Math.round(m * 10) / 10} mins`;
  const h = Math.floor(m / 60);
  const rem = Math.round(m % 60);
  return `${h}h ${rem}m`;
}

/** The Overview's three summary sections - real numbers for the two pages that have them
 *  (see app/core/dashboard_metrics.py), and a plain "coming soon" tile for Workforce
 *  Productivity until the system tracks who reviewed a job and for how long. */
function ExecutiveOverview() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [range, setRange] = useState<DashboardRange>(defaultDashboardRange());
  const [accuracy, setAccuracy] = useState<AccuracyDashboard | null>(null);
  const [processing, setProcessing] = useState<ProcessingTimeDashboard | null>(null);
  const [workforce, setWorkforce] = useState<WorkforceDashboard | null>(null);

  useEffect(() => {
    if (!user?.tenant_id) return;
    const filters = { dateFrom: range.dateFrom, dateTo: range.dateTo, mode: range.mode };
    tenantsApi.getAccuracyDashboard(user.tenant_id, filters).then(setAccuracy).catch(() => setAccuracy(null));
    tenantsApi.getProcessingTimeDashboard(user.tenant_id, filters).then(setProcessing).catch(() => setProcessing(null));
    tenantsApi.getWorkforceDashboard(user.tenant_id, filters).then(setWorkforce).catch(() => setWorkforce(null));
  }, [user?.tenant_id, range.dateFrom, range.dateTo, range.mode]);

  return (
    <div className="mb-8">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-50">Executive Dashboard</h2>
          <p className="text-sm text-slate-500 dark:text-slate-400">Key performance overview</p>
        </div>
      </div>
      <DashboardFilterBar value={range} onChange={setRange} bounds={{ min: "2020-01-01", max: localIso(new Date()) }} />

      <div className="flex flex-col gap-4">
        <Card className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center">
          <div className="flex items-center gap-3 sm:w-48 sm:shrink-0">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400">
              <TargetIcon />
            </div>
            <span className="font-semibold text-slate-900 dark:text-slate-50">Data Accuracy</span>
          </div>
          <div className="flex min-w-[140px] flex-col items-start rounded-xl bg-emerald-50 px-4 py-2.5 dark:bg-emerald-500/10">
            <span className="text-xs font-medium text-emerald-700 dark:text-emerald-300">AI Data Accuracy</span>
            <span className="text-xl font-bold text-emerald-700 dark:text-emerald-300">
              {accuracy?.accuracy_pct != null ? `${accuracy.accuracy_pct}%` : "—"}
            </span>
          </div>
          <div className="grid flex-1 grid-cols-3 gap-4">
            <div>
              <p className="text-xs text-slate-500 dark:text-slate-400">Documents Processed</p>
              <p className="font-semibold text-slate-900 dark:text-slate-50">{(accuracy?.documents_processed ?? 0).toLocaleString()}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500 dark:text-slate-400">Fields Extracted by AI</p>
              <p className="font-semibold text-slate-900 dark:text-slate-50">{(accuracy?.fields_extracted_by_ai ?? 0).toLocaleString()}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500 dark:text-slate-400">Fields Manually Edited</p>
              <p className="font-semibold text-slate-900 dark:text-slate-50">{(accuracy?.fields_manually_edited ?? 0).toLocaleString()}</p>
            </div>
          </div>
          <Button variant="secondary" size="sm" onClick={() => navigate("/tenant-admin/dashboard/accuracy")}>
            See More Details <ArrowRightIcon className="h-4 w-4" />
          </Button>
        </Card>

        <Card className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center">
          <div className="flex items-center gap-3 sm:w-48 sm:shrink-0">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-violet-50 text-violet-600 dark:bg-violet-500/10 dark:text-violet-400">
              <UsersIcon />
            </div>
            <span className="font-semibold text-slate-900 dark:text-slate-50">Workforce Productivity</span>
          </div>
          <div className="flex min-w-[140px] flex-col items-start rounded-xl bg-violet-50 px-4 py-2.5 dark:bg-violet-500/10">
            <span className="text-xs font-medium text-violet-700 dark:text-violet-300">Employee Requirement (FTE)</span>
            <span className="text-xl font-bold text-violet-700 dark:text-violet-300">
              {workforce?.employee_requirement_fte != null ? `${workforce.employee_requirement_fte} FTE` : "—"}
            </span>
          </div>
          <div className="grid flex-1 grid-cols-3 gap-4">
            <div>
              <p className="text-xs text-slate-500 dark:text-slate-400">Avg. Review Time per Job</p>
              <p className="font-semibold text-slate-900 dark:text-slate-50">{fmtMinutes(workforce?.avg_review_minutes_per_job)}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500 dark:text-slate-400">Total Jobs</p>
              <p className="font-semibold text-slate-900 dark:text-slate-50">{(workforce?.total_jobs ?? 0).toLocaleString()}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500 dark:text-slate-400">Human Review Hours</p>
              <p className="font-semibold text-slate-900 dark:text-slate-50">{(workforce?.human_review_hours ?? 0).toLocaleString()} hrs</p>
            </div>
          </div>
          <Button variant="secondary" size="sm" onClick={() => navigate("/tenant-admin/dashboard/workforce")}>
            See More Details <ArrowRightIcon className="h-4 w-4" />
          </Button>
        </Card>

        <Card className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center">
          <div className="flex items-center gap-3 sm:w-48 sm:shrink-0">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-sky-50 text-sky-600 dark:bg-sky-500/10 dark:text-sky-400">
              <ClockIcon />
            </div>
            <span className="font-semibold text-slate-900 dark:text-slate-50">Job Processing Time</span>
          </div>
          <div className="flex min-w-[140px] flex-col items-start rounded-xl bg-sky-50 px-4 py-2.5 dark:bg-sky-500/10">
            <span className="text-xs font-medium text-sky-700 dark:text-sky-300">Avg. Total TAT (End-to-End)</span>
            <span className="text-xl font-bold text-sky-700 dark:text-sky-300">{fmtMinutes(processing?.avg_total_tat_minutes)}</span>
          </div>
          <div className="grid flex-1 grid-cols-4 gap-4">
            <div>
              <p className="text-xs text-slate-500 dark:text-slate-400">Total Jobs</p>
              <p className="font-semibold text-slate-900 dark:text-slate-50">{(processing?.total_jobs ?? 0).toLocaleString()}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500 dark:text-slate-400">Avg. Extraction</p>
              <p className="font-semibold text-slate-900 dark:text-slate-50">{fmtMinutes(processing?.avg_extraction_minutes)}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500 dark:text-slate-400">Avg. Review</p>
              <p className="font-semibold text-slate-900 dark:text-slate-50">{fmtMinutes(processing?.avg_review_minutes)}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500 dark:text-slate-400">Avg. ERP Submission</p>
              <p className="font-semibold text-slate-900 dark:text-slate-50">{fmtMinutes(processing?.avg_erp_submission_minutes)}</p>
            </div>
          </div>
          <Button variant="secondary" size="sm" onClick={() => navigate("/tenant-admin/dashboard/processing-time")}>
            See More Details <ArrowRightIcon className="h-4 w-4" />
          </Button>
        </Card>
      </div>
    </div>
  );
}

/** Tenant Admin's actual landing page — their org's users at a glance (as before), plus what
 *  was missing to make it worth opening: job throughput (reusing the same JobsOverview boxes
 *  Manager's dashboard uses — click one, land on the exact filtered Jobs list), and this
 *  month's billing so far, without a separate trip to Billing & Usage just to check it. */
export function TenantAdminDashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [users, setUsers] = useState<User[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [billing, setBilling] = useState<BillingUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [modeTab, setModeTab] = useState<string>(ALL);

  useEffect(() => {
    async function refresh() {
      try {
        const [u, j] = await Promise.all([usersApi.listUsers(), jobsApi.listJobs()]);
        setUsers(u);
        setJobs(j);
      } catch {
        setUsers([]);
        setJobs([]);
      } finally {
        setLoading(false);
      }
    }
    refresh();
    // Auto-refresh so the boxes stay live without a manual reload - same 30s interval the
    // Manager dashboard uses for the identical full-job-list-just-for-counts fetch.
    const t = window.setInterval(refresh, 30000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    if (!user?.tenant_id) return;
    tenantsApi.getBillingUsage(user.tenant_id).then(setBilling).catch(() => setBilling(null));
  }, [user?.tenant_id]);

  const gk1 = users.filter((u) => u.role === "operator").length;
  const gk2 = users.filter((u) => u.role === "gk2").length;
  const manager = users.filter((u) => u.role === "manager").length;

  const modes = useMemo(() => {
    const s = new Set<string>();
    for (const j of jobs) if (j.mode) s.add(j.mode);
    return [...s].sort();
  }, [jobs]);
  const scopedJobs = useMemo(
    () => (modeTab === ALL ? jobs : jobs.filter((j) => j.mode === modeTab)),
    [jobs, modeTab],
  );

  return (
    <AppShell title="Dashboard" subtitle="Your organization's jobs, users, and billing, at a glance.">
      <ExecutiveOverview />

      <div className="mb-4 border-t border-slate-200 pt-6 dark:border-slate-800">
        <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">Organization</h2>
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Total Users" value={gk1 + gk2 + manager} />
        <StatCard label="Gate Keeper 1" value={gk1} />
        <StatCard label="Gate Keeper 2" value={gk2} />
        <StatCard label="Manager" value={manager} />
      </div>

      {modes.length > 0 && (
        <div className="mb-4 flex gap-1.5 overflow-x-auto">
          {[ALL, ...modes].map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setModeTab(mode)}
              className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                modeTab === mode
                  ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
                  : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
              }`}
            >
              {mode !== ALL && <ModeIcon mode={mode} />}
              {mode}
            </button>
          ))}
        </div>
      )}

      {!loading && <JobsOverview jobs={scopedJobs} groups={["pending", "approval", "completed"]} />}

      <Card className="p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-semibold text-slate-700 dark:text-slate-200">Billing — This Month</h2>
          <Button variant="ghost" size="sm" onClick={() => navigate("/tenant-admin/billing")}>
            View Billing &amp; Usage →
          </Button>
        </div>
        {billing ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div>
              <p className="text-sm text-slate-500 dark:text-slate-400">Jobs</p>
              <p className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-50">
                {billing.total_jobs.toLocaleString()}
              </p>
            </div>
            <div>
              <p className="text-sm text-slate-500 dark:text-slate-400">Pages</p>
              <p className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-50">
                {billing.total_pages.toLocaleString()}
              </p>
            </div>
            <div>
              <p className="text-sm text-slate-500 dark:text-slate-400">Usage Charges</p>
              <p className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-50">
                {formatInr(billing.usage_charges)}
              </p>
            </div>
            <div>
              <p className="text-sm text-slate-500 dark:text-slate-400">Total Amount</p>
              <p className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-50">
                {formatInr(billing.total_amount)}
              </p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-slate-400">No billing data yet.</p>
        )}
      </Card>
    </AppShell>
  );
}
