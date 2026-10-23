import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { RoleBadge } from "./ui/Badge";
import { superAdminFeatures } from "../features/registry";
import * as jobsApi from "../api/jobs";

function initials(name: string): string {
  return name
    .split(" ")
    .map((part) => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

const NAV_ACTIVE =
  "flex items-center gap-2 rounded-lg bg-indigo-50 px-3 py-2 text-sm font-medium text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300";
const NAV_INACTIVE =
  "flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-slate-50";

// One icon per Jobs-list screen, each kept at its own fixed accent colour regardless of
// whether that row is the active one (NAV_ACTIVE/INACTIVE only ever change the row's
// background/text, never these) - a plain page for New, amber document-with-warning for
// Missing Documents, a red warning triangle for ERP Failed, a blue upload for ERP Submitted,
// a green check for ERP Verified.
function NewJobsIcon() {
  return (
    <svg className="h-4 w-4 shrink-0 text-slate-400 dark:text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M7 3h7l5 5v12a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M14 3v5h5" />
    </svg>
  );
}

function MissingDocumentsIcon() {
  return (
    <svg className="h-4 w-4 shrink-0 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M7 3h7l5 5v12a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M14 3v5h5" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 11v3" />
      <circle cx="12" cy="16.5" r="0.75" fill="currentColor" stroke="none" />
    </svg>
  );
}

function ErpFailedIcon() {
  return (
    <svg className="h-4 w-4 shrink-0 text-rose-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v4m0 4h.01M10.29 3.86L1.9 18.4a1 1 0 00.87 1.6h18.46a1 1 0 00.87-1.6L13.71 3.86a1 1 0 00-1.72 0z" />
    </svg>
  );
}

function ErpSubmittedIcon() {
  return (
    <svg className="h-4 w-4 shrink-0 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M4.5 20h15" />
    </svg>
  );
}

function ErpVerifiedIcon() {
  return (
    <svg className="h-4 w-4 shrink-0 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <circle cx="12" cy="12" r="9" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M8.5 12.5l2.5 2.5 4.5-5" />
    </svg>
  );
}

function CountBadge({ count, cls }: { count: number; cls: string }) {
  return (
    <span className={`ml-auto inline-flex min-w-[1.375rem] shrink-0 items-center justify-center rounded-full px-1.5 py-0.5 text-[11px] font-semibold leading-none ${cls}`}>
      {count}
    </span>
  );
}

export function AppShell({
  title,
  subtitle,
  actions,
  sidebar,
  children,
}: {
  title: string;
  /** ReactNode, not string: the job header puts an icon beside the words. */
  subtitle?: ReactNode;
  actions?: ReactNode;
  /** Extra navigation for the page you are on, shown in the REAL left rail under the app's
   *  own links — the job's stages, for instance. A page-specific list floating in the content
   *  area beside a sidebar reads as a second, competing navigation; there should be one. */
  sidebar?: ReactNode;
  children?: ReactNode;
}) {
  const { user, logout } = useAuth();
  const location = useLocation();
  // The page you are actually on is what should be lit up — a nav rail that always shows
  // "Dashboard" as current, even while you are looking at Jobs, is worse than no highlight
  // at all, because it actively tells you the wrong thing.
  const navClass = (path: string) => (location.pathname === path ? NAV_ACTIVE : NAV_INACTIVE);

  // The Jobs list's five screens - New Jobs (Pre-Alert through anything short of the ERP
  // submission actually running), Missing Documents (a mandatory document hasn't arrived yet -
  // see outer_status "Missing Documents"), ERP Failed Jobs (status or gk2_status "failed"),
  // Submitted Jobs (the AI/ERP automation already went through; each row there gets a Verified
  // button), Completed Jobs (confirmed, or completed the direct Submit Entry way). Every role
  // that sees a plain Jobs link gets all five - see Job.category/_category_condition in
  // api/v1/jobs.py; applies identically whether the tenant/mode requires GK2 review or not.
  const JOB_CATEGORIES: {
    key: "new" | "missing" | "failed" | "submitted" | "completed";
    label: string;
    icon: () => ReactNode;
    badgeCls: string;
  }[] = [
    { key: "new", label: "New Jobs", icon: NewJobsIcon,
      badgeCls: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300" },
    { key: "missing", label: "Missing Documents", icon: MissingDocumentsIcon,
      badgeCls: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300" },
    { key: "failed", label: "ERP Failed Jobs", icon: ErpFailedIcon,
      badgeCls: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300" },
    { key: "submitted", label: "ERP Submitted Jobs", icon: ErpSubmittedIcon,
      badgeCls: "bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300" },
    { key: "completed", label: "ERP Verified Jobs", icon: ErpVerifiedIcon,
      badgeCls: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" },
  ];
  const activeJobCategory = location.pathname === "/jobs"
    ? new URLSearchParams(location.search).get("category")
    : null;

  // Live badge counts for the five links above - fetched once a role that actually shows them
  // logs in, then refreshed every 30s (same interval the operator dashboard's own live counts
  // already poll at) so a job moving between screens updates the sidebar without a reload.
  const showsJobCategories =
    user != null && ["operator", "admin", "gk2", "manager"].includes(user.role);
  const [categoryCounts, setCategoryCounts] = useState<jobsApi.JobCategoryCounts | null>(null);
  useEffect(() => {
    if (!showsJobCategories) return;
    let alive = true;
    const load = () => {
      jobsApi.getJobCategoryCounts().then((c) => { if (alive) setCategoryCounts(c); }).catch(() => {});
    };
    load();
    const t = window.setInterval(load, 30000);
    return () => { alive = false; window.clearInterval(t); };
  }, [showsJobCategories]);

  function jobCategoryLinks() {
    return JOB_CATEGORIES.map(({ key, label, icon: Icon, badgeCls }) => (
      <Link
        key={key}
        to={`/jobs?category=${key}`}
        className={activeJobCategory === key ? NAV_ACTIVE : NAV_INACTIVE}
      >
        <Icon />
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {categoryCounts && <CountBadge count={categoryCounts[key]} cls={badgeCls} />}
      </Link>
    ));
  }
  const dashboardPath =
    user?.role === "super_admin"
      ? "/super-admin"
      : user?.role === "tenant_admin"
        ? "/tenant-admin"
        : user?.role === "manager"
          ? "/manager"
          : user?.role === "admin" || user?.role === "gk2"
            ? "/jobs"
            : "/operator";

  return (
    <div className="flex h-svh overflow-hidden bg-slate-50 dark:bg-slate-950">
      <aside className="flex h-svh w-64 shrink-0 flex-col border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center gap-2.5 px-6 py-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 text-sm font-bold text-white">
            C
          </div>
          <span className="text-lg font-semibold tracking-tight text-slate-900 dark:text-slate-50">Cargora</span>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-4">
          {/* A page with its own rail REPLACES the app links rather than adding to them.
              Inside a job the seven stages are the navigation; leaving Dashboard and Jobs
              above them gave two lists doing different jobs in one column, and the way back
              out is the "← All jobs" link at the top of the page itself. */}
          {sidebar ?? (
            <>
          <Link to={dashboardPath} className={navClass(dashboardPath)}>
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
            </svg>
            Dashboard
          </Link>

          {user?.role === "super_admin" && (
            <div className="mt-8">
              <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Platform Features</h3>
              <div className="mt-2 space-y-1">
                {superAdminFeatures.map((f) => (
                  <Link key={f.path} to={f.path} className={navClass(f.path)}>
                    {f.navLabel}
                  </Link>
                ))}
              </div>
            </div>
          )}

          {user?.role === "tenant_admin" && (
            <div className="mt-8">
              <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Workspace</h3>
              <div className="mt-2 space-y-1">
                {[
                  { to: "/tenant-admin/customers", label: "Shipment Type" },
                  { to: "/tenant-admin/masters", label: "Masters" },
                  { to: "/tenant-admin/users", label: "Users" },
                  { to: "/tenant-admin/billing", label: "Billing & Usage" },
                  { to: "/account", label: "Settings" },
                ].map((item) => (
                  <Link key={item.to} to={item.to} className={navClass(item.to)}>
                    {item.label}
                  </Link>
                ))}
              </div>
            </div>
          )}

          {user?.role === "operator" && (
            <div className="mt-8">
              <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Workspace</h3>
              <div className="mt-2 space-y-1">
                {jobCategoryLinks()}
                <Link to="/account" className={navClass("/account")}>
                  Settings
                </Link>
              </div>
            </div>
          )}

          {user?.role === "admin" && (
            <div className="mt-8">
              <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Workspace</h3>
              <div className="mt-2 space-y-1">
                {jobCategoryLinks()}
              </div>
            </div>
          )}

          {user?.role === "gk2" && (
            <div className="mt-8">
              <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Workspace</h3>
              <div className="mt-2 space-y-1">
                {jobCategoryLinks()}
                <Link to="/account" className={navClass("/account")}>
                  Settings
                </Link>
              </div>
            </div>
          )}

          {user?.role === "manager" && (
            <div className="mt-8">
              <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Workspace</h3>
              <div className="mt-2 space-y-1">
                {jobCategoryLinks()}
              </div>
            </div>
          )}
            </>
          )}
        </nav>

        <div className="border-t border-slate-200 p-4 dark:border-slate-800">
          <div className="mb-3 flex items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-slate-200 text-xs font-semibold text-slate-700 dark:bg-slate-700 dark:text-slate-200">
              {user ? initials(user.full_name) : ""}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">{user?.full_name}</p>
              <p className="truncate text-xs text-slate-500 dark:text-slate-400">{user?.email}</p>
            </div>
          </div>
          {user && <RoleBadge role={user.role} />}
          <button
            type="button"
            onClick={() => logout()}
            className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
            </svg>
            Log out
          </button>
        </div>
      </aside>

      <div className="flex-1 overflow-y-auto">
        {/* Sticky: on a long job screen the title, the status and the way back all scrolled
            away, so which job you were in and how it was doing had to be remembered rather
            than read. z-20 keeps it over the pinned document preview underneath. */}
        <header className="sticky top-0 z-20 flex items-center justify-between gap-4 border-b border-slate-200 bg-white/90 px-8 py-4 backdrop-blur-sm dark:border-slate-800 dark:bg-slate-900/90">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-900 dark:text-slate-50">{title}</h1>
            {subtitle && <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
          </div>
          {actions}
        </header>
        <main className="p-8">{children}</main>
      </div>
    </div>
  );
}
