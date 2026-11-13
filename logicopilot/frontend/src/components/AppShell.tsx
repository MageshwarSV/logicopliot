import { useEffect, useRef, useState, type ReactNode, type SVGProps } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { RoleBadge } from "./ui/Badge";
import { superAdminFeatures, SUPER_ADMIN_FEATURE_GROUPS } from "../features/registry";
import * as jobsApi from "../api/jobs";
import { NotificationBell } from "./NotificationBell";
import { ChangePasswordModal } from "./ChangePasswordModal";
import {
  LayersIcon, DocumentIcon, CreditCardIcon, UsersIcon, DatabaseIcon, GearIcon,
  BriefcaseIcon, ChartBarIcon, ChevronDownIcon, BuildingIcon, MailIcon,
  CodeIcon, MonitorIcon, ArrowPathIcon, UserIcon, LockIcon, LogoutIcon,
} from "./ui/icons";

// One icon per Super Admin nav item, keyed by path - kept here (not in registry.js, which is
// pure data only, no component imports, to avoid a cycle with this file) rather than as a
// third parallel array to keep in sync by hand.
const SUPER_ADMIN_ICON_BY_PATH: Record<string, (props: SVGProps<SVGSVGElement>) => ReactNode> = {
  "/super-admin/tenants": BuildingIcon,
  "/super-admin/users": UsersIcon,
  "/super-admin/template-list": DocumentIcon,
  "/super-admin/template-creation": LayersIcon,
  "/super-admin/data-transformation": ArrowPathIcon,
  "/super-admin/email-puller": MailIcon,
  "/super-admin/erp-scripts": CodeIcon,
  "/super-admin/entry-browser": MonitorIcon,
  "/jobs": BriefcaseIcon,
  "/super-admin/billing": CreditCardIcon,
  "/super-admin/settings": GearIcon,
};

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
  categoryCountFilters,
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
  /** The Jobs list's OWN currently-applied filters (JobsPage.tsx) - passed down so the five
   *  sidebar badges below count exactly the same filtered population the list itself is
   *  showing, instead of a flat unfiltered total that could disagree with it (found live: a
   *  badge read 7 while its own filtered list, "Last 7 days" + assigned to "malar", showed 1).
   *  undefined on every other page, which keeps counting everything, unchanged. */
  categoryCountFilters?: jobsApi.JobCategoryCountFilters;
}) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const [masterDataOpen, setMasterDataOpen] = useState(
    location.pathname.startsWith("/tenant-admin/masters"),
  );
  // The page you are actually on is what should be lit up — a nav rail that always shows
  // "Dashboard" as current, even while you are looking at Jobs, is worse than no highlight
  // at all, because it actively tells you the wrong thing.
  const navClass = (path: string) => (location.pathname === path ? NAV_ACTIVE : NAV_INACTIVE);

  // The Jobs list's four screens - New Jobs (Pre-Alert through anything short of the ERP
  // submission actually running), Missing Documents (a mandatory document hasn't arrived yet -
  // see outer_status "Missing Documents"), ERP Failed Jobs (status or gk2_status "failed"),
  // ERP Submitted Jobs (the AI/ERP automation went through, automated or direct - no separate
  // "verified" state any more, that screen was removed 2026-11-10 by explicit request since a
  // submitted entry counts the same whether or not it has also been verified). Every role
  // that sees a plain Jobs link gets all four - see Job.category/_category_condition in
  // api/v1/jobs.py; applies identically whether the tenant/mode requires GK2 review or not.
  const JOB_CATEGORIES: {
    key: "new" | "missing" | "failed" | "submitted";
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
  ];
  const activeJobCategory = location.pathname === "/jobs"
    ? new URLSearchParams(location.search).get("category")
    : null;

  // Live badge counts for the five links above - fetched once a role that actually shows them
  // logs in, then refreshed every 30s (same interval the operator dashboard's own live counts
  // already poll at) so a job moving between screens updates the sidebar without a reload. Also
  // refetched the moment any of the Jobs list's OWN filters change (categoryCountFilters, from
  // JobsPage.tsx) - destructured into individual primitive deps rather than depending on the
  // object itself, which JobsPage passes as a new reference every render and would otherwise
  // refetch on every keystroke-unrelated re-render too.
  const showsJobCategories =
    user != null && ["operator", "admin", "gk2", "manager", "tenant_admin"].includes(user.role);
  const [categoryCounts, setCategoryCounts] = useState<jobsApi.JobCategoryCounts | null>(null);
  const ccfDateFrom = categoryCountFilters?.dateFrom;
  const ccfDateTo = categoryCountFilters?.dateTo;
  const ccfAssignedTo = categoryCountFilters?.assignedTo;
  const ccfCustomer = categoryCountFilters?.customer;
  const ccfStatus = categoryCountFilters?.status;
  const ccfShipmentType = categoryCountFilters?.shipmentType;
  useEffect(() => {
    if (!showsJobCategories) return;
    let alive = true;
    const load = () => {
      jobsApi.getJobCategoryCounts({
        dateFrom: ccfDateFrom, dateTo: ccfDateTo, assignedTo: ccfAssignedTo,
        customer: ccfCustomer, status: ccfStatus, shipmentType: ccfShipmentType,
      }).then((c) => { if (alive) setCategoryCounts(c); }).catch(() => {});
    };
    load();
    const t = window.setInterval(load, 30000);
    return () => { alive = false; window.clearInterval(t); };
  }, [showsJobCategories, ccfDateFrom, ccfDateTo, ccfAssignedTo, ccfCustomer, ccfStatus, ccfShipmentType]);

  const totalJobsCount = categoryCounts
    ? Object.values(categoryCounts).reduce((a, b) => a + b, 0)
    : null;

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
          : user?.role === "saas_finance"
            ? "/saas-finance"
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
            <>
              {/* "/super-admin" itself (tenant+admin management) is NOT repeated here - the
                  universal Dashboard link above already points there. */}
              {SUPER_ADMIN_FEATURE_GROUPS.map((group) => (
                <div key={group} className="mt-8">
                  <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                    {group}
                  </h3>
                  <div className="mt-2 space-y-1">
                    {superAdminFeatures.filter((f) => f.group === group).map((f) => {
                      const Icon = SUPER_ADMIN_ICON_BY_PATH[f.path];
                      return (
                        <Link key={f.path} to={f.path} className={navClass(f.path)}>
                          {Icon && <Icon />}
                          {f.navLabel}
                        </Link>
                      );
                    })}
                  </div>
                </div>
              ))}
            </>
          )}

          {user?.role === "tenant_admin" && (
            <>
              <div className="mt-8">
                <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Billing &amp; Finance</h3>
                <div className="mt-2 space-y-1">
                  <Link to="/tenant-admin/billing" className={navClass("/tenant-admin/billing")}>
                    <LayersIcon /> Usage &amp; Consumption
                  </Link>
                  <Link to="/tenant-admin/invoices" className={navClass("/tenant-admin/invoices")}>
                    <DocumentIcon /> Invoices
                  </Link>
                  <Link to="/tenant-admin/subscription" className={navClass("/tenant-admin/subscription")}>
                    <CreditCardIcon /> Subscription &amp; Plans
                  </Link>
                </div>
              </div>

              <div className="mt-8">
                <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Administration</h3>
                <div className="mt-2 space-y-1">
                  <Link to="/tenant-admin/users" className={navClass("/tenant-admin/users")}>
                    <UsersIcon /> Users &amp; Access
                  </Link>

                  <button
                    type="button"
                    onClick={() => setMasterDataOpen((v) => !v)}
                    className={`${NAV_INACTIVE} w-full justify-between`}
                  >
                    <span className="flex items-center gap-2">
                      <DatabaseIcon /> Master Data
                    </span>
                    <ChevronDownIcon
                      className={`h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform ${masterDataOpen ? "rotate-180" : ""}`}
                    />
                  </button>
                  {masterDataOpen && (
                    <div className="ml-3 space-y-0.5 border-l border-slate-200 pl-3 dark:border-slate-700">
                      {[
                        { label: "Shipments", to: "/tenant-admin/customers" },
                        { label: "Shipment Type", to: "/tenant-admin/masters" },
                        { label: "Ports", to: "/tenant-admin/masters" },
                        { label: "Consignees", to: "/tenant-admin/masters" },
                        { label: "Others", to: "/tenant-admin/masters" },
                      ].map((item) => (
                        <Link
                          key={item.label}
                          to={item.to}
                          className="flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-50"
                        >
                          <span className="h-1 w-1 rounded-full bg-slate-400" />
                          {item.label}
                        </Link>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div className="mt-8">
                <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Operations</h3>
                <div className="mt-2 space-y-1">
                  <Link to="/jobs" className={navClass("/jobs")}>
                    <BriefcaseIcon />
                    <span className="min-w-0 flex-1 truncate">Jobs</span>
                    {totalJobsCount != null && (
                      <CountBadge count={totalJobsCount} cls="bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" />
                    )}
                  </Link>
                  <Link to="/tenant-admin/reports" className={navClass("/tenant-admin/reports")}>
                    <ChartBarIcon /> Reports
                  </Link>
                </div>
              </div>
            </>
          )}

          {user?.role === "operator" && (
            <div className="mt-8">
              <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Workspace</h3>
              <div className="mt-2 space-y-1">
                {jobCategoryLinks()}
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

          {user?.role === "saas_finance" && (
            <div className="mt-8">
              <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Finance</h3>
              <div className="mt-2 space-y-1">
                {[
                  { to: "/saas-finance", label: "Dashboard" },
                  { to: "/saas-finance/customers", label: "Customers" },
                  { to: "/saas-finance/usage-billing", label: "Usage & Billing" },
                  { to: "/saas-finance/invoices", label: "Invoices" },
                  { to: "/saas-finance/payments", label: "Payments" },
                  { to: "/saas-finance/pricing-plans", label: "Pricing & Plans" },
                  { to: "/saas-finance/reports", label: "Reports" },
                ].map((item) => (
                  <Link key={item.to} to={item.to} className={navClass(item.to)}>
                    {item.label}
                  </Link>
                ))}
              </div>
            </div>
          )}
            </>
          )}
        </nav>

        <UserMenu
          user={user}
          initials={user ? initials(user.full_name) : ""}
          onLogout={() => logout()}
        />
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
          {/* The bell lives in the SHELL, not on any one page: an ERP submission finishes
              minutes after the operator left that job, so it has to reach them wherever they
              happen to be. Left of the page's own actions so the primary call to action on
              each screen (+ New Job, Submit Entry) stays furthest right where it is expected. */}
          <div className="flex items-center gap-3">
            <NotificationBell />
            {actions}
          </div>
        </header>
        <main className="p-8">{children}</main>
      </div>
    </div>
  );
}


/** The account card at the bottom of the sidebar, and the menu it opens: Profile, Change
 *  Password, Log out. This replaced the separate "Settings" sidebar link (it only ever led to
 *  the same account page) and the always-visible Log out button. The menu opens upward, since
 *  the card sits at the very bottom of the screen. */
function UserMenu({
  user, initials, onLogout,
}: {
  user: ReturnType<typeof useAuth>["user"];
  initials: string;
  onLogout: () => void;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [changePasswordOpen, setChangePasswordOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const item =
    "flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm text-slate-700 transition-colors hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800";

  return (
    <div ref={rootRef} className="relative border-t border-slate-200 p-4 dark:border-slate-800">
      {open && (
        <div
          role="menu"
          className="absolute inset-x-4 bottom-full z-30 mb-2 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg shadow-slate-900/10 dark:border-slate-700 dark:bg-slate-900"
        >
          <button type="button" role="menuitem" className={item} onClick={() => { setOpen(false); navigate("/account"); }}>
            <UserIcon className="h-4 w-4 text-slate-500" /> Profile
          </button>
          <button type="button" role="menuitem" className={item} onClick={() => { setOpen(false); setChangePasswordOpen(true); }}>
            <LockIcon className="h-4 w-4 text-slate-500" /> Change Password
          </button>
          <div className="my-1 border-t border-slate-100 dark:border-slate-800" />
          <button type="button" role="menuitem" className={item} onClick={() => { setOpen(false); onLogout(); }}>
            <LogoutIcon className="h-4 w-4 text-slate-500" /> Log out
          </button>
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="-m-2 flex w-[calc(100%+1rem)] items-center gap-3 rounded-lg p-2 text-left transition-colors hover:bg-slate-50 dark:hover:bg-slate-800"
      >
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-slate-200 text-xs font-semibold text-slate-700 dark:bg-slate-700 dark:text-slate-200">
          {initials}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">{user?.full_name}</p>
          <p className="truncate text-xs text-slate-500 dark:text-slate-400">{user?.email}</p>
        </div>
        <ChevronDownIcon className={`h-4 w-4 shrink-0 text-slate-500 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {user && <div className="mt-3"><RoleBadge role={user.role} /></div>}

      <ChangePasswordModal open={changePasswordOpen} onClose={() => setChangePasswordOpen(false)} />
    </div>
  );
}
