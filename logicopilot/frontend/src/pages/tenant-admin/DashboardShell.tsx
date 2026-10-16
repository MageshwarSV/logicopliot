import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { JobsOverview } from "../../components/JobsOverview";
import { AppShell } from "../../components/AppShell";
import { Card, StatCard } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { ModeIcon } from "../../components/ModeIcon";
import { useAuth } from "../../auth/useAuth";
import * as usersApi from "../../api/users";
import * as jobsApi from "../../api/jobs";
import * as tenantsApi from "../../api/tenants";
import type { BillingUsage } from "../../api/tenants";
import type { User } from "../../types/auth";
import type { Job } from "../../types/jobs";

const ALL = "All";

function formatInr(n: number): string {
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
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
