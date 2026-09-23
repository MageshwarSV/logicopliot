import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { RoleBadge } from "./ui/Badge";
import { superAdminFeatures } from "../features/registry";

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
                <Link
                  to="/jobs"
                  className={navClass("/jobs")}
                >
                  Jobs
                </Link>
              </div>
            </div>
          )}

          {user?.role === "admin" && (
            <div className="mt-8">
              <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Workspace</h3>
              <div className="mt-2 space-y-1">
                <Link
                  to="/jobs"
                  className={navClass("/jobs")}
                >
                  Jobs — every tenant
                </Link>
              </div>
            </div>
          )}

          {user?.role === "gk2" && (
            <div className="mt-8">
              <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Workspace</h3>
              <div className="mt-2 space-y-1">
                <Link
                  to="/jobs"
                  className={navClass("/jobs")}
                >
                  Jobs
                </Link>
              </div>
            </div>
          )}

          {user?.role === "manager" && (
            <div className="mt-8">
              <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Workspace</h3>
              <div className="mt-2 space-y-1">
                <Link
                  to="/jobs"
                  className={navClass("/jobs")}
                >
                  Jobs — read only
                </Link>
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
