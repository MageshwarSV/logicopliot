import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { ModeIcon } from "../../components/ModeIcon";
import { BUCKETS, GROUP_LABELS } from "../../components/JobsOverview";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { DataTable, type Column } from "../../components/ui/DataTable";
import { Modal } from "../../components/ui/Modal";
import { Select } from "../../components/ui/Input";
import { Alert } from "../../components/ui/Alert";
import * as jobsApi from "../../api/jobs";
import * as emailApi from "../../api/email";
import * as tenantsApi from "../../api/tenants";
import * as usersApi from "../../api/users";
import type { Job } from "../../types/jobs";
import type { Tenant } from "../../types/tenant";
import type { User } from "../../types/auth";
import { useAuth } from "../../auth/useAuth";

// How many rows load at a time — loading a tenant's entire history in one request is what
// was making this page slow to open. Scrolling near the bottom loads the next batch.
const PAGE_SIZE = 20;
const QUICK_ALL = "__all__";

export function JobsPage() {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  // Set by pressing one of the operator dashboard's boxes — the list then shows exactly
  // what that box counted, server-side (see group/bucket in app/api/v1/jobs.py).
  const group = searchParams.get("group");
  const bucket = searchParams.get("bucket") ?? "all";
  // Only these two roles ever see jobs outside their own tenant — the tenant filter and
  // column are pointless (and the /tenants call would just 403) for anyone else.
  const crossTenant = user?.role === "admin" || user?.role === "super_admin";
  const [jobs, setJobs] = useState<Job[]>([]);
  const jobsRef = useRef<Job[]>([]);
  useEffect(() => {
    jobsRef.current = jobs;
  }, [jobs]);
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [groups, setGroups] = useState<jobsApi.AvailableGroup[]>([]);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantFilter, setTenantFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [groupId, setGroupId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [checkingMail, setCheckingMail] = useState(false);
  const [mailMsg, setMailMsg] = useState<string | null>(null);
  // Which row's "⋮" menu is open — one at a time, closed by its own backdrop click.
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  // Mode tabs: a personal-assignment filter, not a general one — only shown for operator/gk2,
  // and only when that user actually has modes assigned (assigned_modes on their own profile,
  // already loaded at login — no extra request). "All" plus one tab per assigned mode.
  // Applied client-side to whatever has loaded so far, same as before pagination existed.
  const assignedModes = user?.modes ?? [];
  const showModeTabs = (user?.role === "operator" || user?.role === "gk2") && assignedModes.length > 0;
  const [activeModeTab, setActiveModeTab] = useState<string>("All");
  const [savingEtaId, setSavingEtaId] = useState<string | null>(null);
  const canEditEta = user?.role === "operator" || user?.role === "super_admin" || user?.role === "admin";
  // Who can assign a job to an operator — same roles the backend's PATCH /jobs/{id}/assign
  // accepts. Always editable, even after a job already has someone assigned — reassigning
  // just overwrites the previous value, there is no lock to work around.
  const canAssign = user?.role === "operator" || user?.role === "super_admin" || user?.role === "admin";
  const [operators, setOperators] = useState<User[]>([]);
  const [savingAssignId, setSavingAssignId] = useState<string | null>(null);
  // Quick filter — date range + customer. Lives here, not on the dashboard, so it only ever
  // narrows what you are already looking at. Applied client-side to whatever has loaded so
  // far (same as the mode tabs), since it is a personal refinement rather than the kind of
  // thing worth a dedicated server filter the way the dashboard's group/bucket boxes are.
  // Defaults to "today" for a plain visit to Jobs — but NOT when arriving from a dashboard
  // box (group/bucket set): that click already means "show me exactly what that box
  // counted", server-side, for whatever bucket was pressed (This Week/Month/Overall
  // included). Stacking a client-side "today" filter on top of an "Overall" bucket click
  // would have hidden almost everything the box just promised to show.
  const [quick, setQuick] = useState<"today" | "7" | "30" | "all">(() => (group ? "all" : "today"));
  const [customer, setCustomer] = useState<string>(QUICK_ALL);
  const [statusFilter, setStatusFilter] = useState<string>(QUICK_ALL);
  const [assignedTo, setAssignedTo] = useState<string>(QUICK_ALL);
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");

  async function loadFirstPage() {
    setLoading(true);
    setError(null);
    try {
      const [jobList, groupList] = await Promise.all([
        jobsApi.listJobs({
          tenantId: tenantFilter || undefined,
          limit: PAGE_SIZE,
          offset: 0,
          group: group ?? undefined,
          bucket,
        }),
        jobsApi.listAvailableGroups(),
      ]);
      setJobs(jobList);
      setHasMore(jobList.length === PAGE_SIZE);
      setGroups(groupList);
      if (groupList[0]) setGroupId(groupList[0].id);
    } catch {
      setError("Failed to load jobs. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }

  async function loadMore() {
    // The sentinel can already be "in view" the instant the page mounts — the table is
    // empty while `loading` is true, so the whole page is short enough to put it in the
    // viewport before loadFirstPage has even resolved. Without this guard that races
    // loadFirstPage's own fetch and appends a duplicate first page on top of it.
    if (loading || loadingMore || !hasMore || pastDateWindow) return;
    setLoadingMore(true);
    try {
      const more = await jobsApi.listJobs({
        tenantId: tenantFilter || undefined,
        limit: PAGE_SIZE,
        offset: jobsRef.current.length,
        group: group ?? undefined,
        bucket,
      });
      setJobs((prev) => [...prev, ...more]);
      setHasMore(more.length === PAGE_SIZE);
    } catch {
      // The sentinel just won't advance again until it re-enters view; rows already
      // loaded stay exactly as they are.
    } finally {
      setLoadingMore(false);
    }
  }

  // Re-fetches exactly the window already loaded, to catch status changes live (ERP outcomes,
  // a fresh GK2 approval) WITHOUT resetting scroll position or re-triggering "load more".
  async function refreshCurrentWindow() {
    const count = Math.max(jobsRef.current.length, PAGE_SIZE);
    try {
      const fresh = await jobsApi.listJobs({
        tenantId: tenantFilter || undefined,
        limit: count,
        offset: 0,
        group: group ?? undefined,
        bucket,
      });
      setJobs(fresh);
      setHasMore(fresh.length === count);
    } catch {
      // Leave the current view as-is — a missed refresh tick is not worth an error banner.
    }
  }

  useEffect(() => {
    if (crossTenant) tenantsApi.listTenants().then(setTenants).catch(() => {});
  }, [crossTenant]);

  // The assign dropdown's candidate list — every operator in the tenant, filtered per-row to
  // whichever ones are scoped to that job's own mode. Fetched once; nothing about it changes
  // per row so there is no reason to re-fetch it.
  useEffect(() => {
    if (canAssign) usersApi.listUsers().then(setOperators).catch(() => {});
  }, [canAssign]);

  useEffect(() => {
    loadFirstPage();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantFilter, group, bucket]);

  useEffect(() => {
    // 15s, not 4s - this re-fetches every row currently loaded (which grows with how far an
    // operator has scrolled, uncapped), fully enriched per job. A 4s interval across every
    // open Jobs list tab was part of what exhausted the database connection pool under real
    // concurrent use (see the dashboards' identical change) - 15s is still well inside "feels
    // live" for a status change, at well under half the request volume.
    const t = window.setInterval(refreshCurrentWindow, 15000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantFilter, group, bucket]);

  // Infinite scroll: a sentinel just past the table loads the next batch once it is close to
  // view, so scrolling down reads as "it just keeps going" rather than a page-by-page click.
  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    // AppShell's own content pane scrolls (it's `overflow-y-auto`), not the browser
    // viewport/body — an observer rooted at the viewport never sees this sentinel move
    // relative to it, so scrolling never appeared to load anything.
    const root = el.closest(".overflow-y-auto") as HTMLElement | null;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) loadMore();
      },
      { root, rootMargin: "200px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
    // quick/dateFrom/dateTo: so a filter change re-captures loadMore's CURRENT pastDateWindow
    // guard immediately, rather than only once jobs.length/hasMore/etc happen to change too.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs.length, hasMore, loading, loadingMore, tenantFilter, group, bucket, quick, dateFrom, dateTo]);

  async function checkEmail() {
    setCheckingMail(true);
    setMailMsg(null);
    setError(null);
    try {
      const res = await emailApi.pullEmail();
      if (!res.ok) {
        setMailMsg(`Mailbox error: ${res.error ?? "unavailable"}`);
      } else if (res.note) {
        setMailMsg(res.note);
      } else {
        const created = (res.processed ?? []).filter((m) => m.job_id);
        setMailMsg(
          created.length
            ? `Pulled ${created.length} new job${created.length === 1 ? "" : "s"} from email.`
            : "Checked — no new document emails for you.",
        );
      }
      await loadFirstPage();
    } catch {
      setMailMsg("Could not check email.");
    } finally {
      setCheckingMail(false);
    }
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const job = await jobsApi.createJob({ group_id: groupId }); // Job No auto-generated
      setModalOpen(false);
      // Its own tab, like every other way of opening a job — the list the operator was
      // working through stays put behind it.
      window.open(`/jobs/${job.id}`, "_blank", "noopener");
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not create the job.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleEtaChange(job: Job, value: string) {
    setSavingEtaId(job.id);
    setError(null);
    try {
      await jobsApi.setJobEta(job.id, value || null);
      await refreshCurrentWindow();
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not set the ETA.");
    } finally {
      setSavingEtaId(null);
    }
  }

  async function handleAssignChange(job: Job, operatorId: string) {
    setSavingAssignId(job.id);
    setError(null);
    try {
      await jobsApi.assignJobOperator(job.id, operatorId || null);
      await refreshCurrentWindow();
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not assign the operator.");
    } finally {
      setSavingAssignId(null);
    }
  }

  async function handleDelete(job: Job) {
    if (!window.confirm(`Delete job ${job.reference}? This removes its documents and extracted values.`)) return;
    setDeletingId(job.id);
    setError(null);
    try {
      await jobsApi.deleteJob(job.id);
      await refreshCurrentWindow();
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not delete the job.");
    } finally {
      setDeletingId(null);
    }
  }

  // The busy words — extraction running, GK2's placeholder ERP prep, or the real ERP
  // submission in flight — get the same pulsing sky dot; everything else is a place the job
  // is WAITING, not something happening.
  const BUSY = new Set([
    "Running", "AI - Processing", "AI - Preparing for ERP", "ERP Entry Process Started", "Submitted",
  ]);

  function stageBadge(stage?: string | null) {
    const s = stage ?? "Document Capture";
    const cls =
      s === "Completed" || s === "AI - ERP Submitted"
        ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
        : s === "Failed"
          ? "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"
        : s === "Duplicate"
          ? "bg-orange-50 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300"
        : s === "Possible Duplicate"
          ? "bg-amber-50 text-amber-800 ring-1 ring-amber-300 dark:bg-amber-500/10 dark:text-amber-300"
        : s === "Hold"
          ? "bg-amber-50 text-amber-800 ring-1 ring-amber-300 dark:bg-amber-500/10 dark:text-amber-300"
      : BUSY.has(s)
          ? "bg-sky-50 text-sky-700 dark:bg-sky-500/10 dark:text-sky-300"
        : s === "Pending GK2 Approval"
          ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
        : s === "ERP Submission" || s === "Ready for Submission"
          ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
          : s === "Data Validation" || s === "Data Extraction" || s === "Ready for Review" ||
            s === "Review in Progress" || s === "GK1 Review" || s === "GK1 Reviewing"
            ? "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300"
            : s === "Pending Documents" || s === "Pre-Alert Received"
              ? "bg-cyan-50 text-cyan-700 dark:bg-cyan-500/10 dark:text-cyan-300"
              : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300";
    return (
      <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
        {BUSY.has(s) && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-500" />}
        {s}
      </span>
    );
  }

  function when(iso?: string | null, withTime = true) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString(undefined, {
      day: "2-digit", month: "short",
      ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
    });
  }

  const customers = useMemo(() => {
    const s = new Set<string>();
    for (const j of jobs) if (j.customer_name) s.add(j.customer_name);
    return [...s].sort();
  }, [jobs]);

  // Exactly the same string the Status column itself shows — filtering by anything else
  // would let a person pick a status they can never actually match against a row.
  const statuses = useMemo(() => {
    const s = new Set<string>();
    for (const j of jobs) {
      const st = j.outer_status ?? j.stage;
      if (st) s.add(st);
    }
    return [...s].sort();
  }, [jobs]);

  // Same rule as statuses above: exactly the string the Assigned To column itself shows
  // (operator name, else who it was pulled from, else nothing) - never a raw operator id,
  // which the operator picking from this list would have no way to recognise or match.
  const assignedToLabel = (j: Job) => j.operator_name ?? j.pulled_from_sender ?? "";
  const assignees = useMemo(() => {
    const s = new Set<string>();
    for (const j of jobs) {
      const label = assignedToLabel(j);
      if (label) s.add(label);
    }
    return [...s].sort();
  }, [jobs]);

  // The window the quick filter means, unless explicit dates override it.
  const quickRange = useMemo(() => {
    if (dateFrom || dateTo) return { from: dateFrom, to: dateTo };
    if (quick === "all") return { from: "", to: "" };
    const days = quick === "today" ? 0 : Number(quick) - 1;
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - days);
    return { from: start.toISOString().slice(0, 10), to: "" };
  }, [quick, dateFrom, dateTo]);

  function dayKey(iso: string | null | undefined): string {
    if (!iso) return "";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "" : d.toISOString().slice(0, 10);
  }

  // Jobs arrive newest-first (the server sorts by created_at desc). The quick/date-range,
  // customer and status filters below are applied client-side, on top of whatever has been
  // fetched so far — the server-side pagination knows nothing about them. Without this guard,
  // a narrow filter (the DEFAULT "Today" view, most of all) leaves very few visible rows, so
  // the infinite-scroll sentinel never leaves view and the loader keeps paging through the
  // WHOLE unfiltered history in the background, one page at a time — "Loading more…" blinking
  // long after the filtered view already shows everything it ever will.
  // Once the OLDEST job fetched so far is already older than the active date filter's own
  // start, every job the server would send next is older still (same descending sort), so a
  // "from" date filter can never gain anything from further pages — the search has already
  // gone further back than it needs to. A "to"-only filter has no such shortcut (the pages
  // still being skipped are the newest ones, and older pages may yet fall inside the window),
  // so it is deliberately left to page normally.
  const pastDateWindow = useMemo(() => {
    if (!quickRange.from || jobs.length === 0) return false;
    const oldest = dayKey(jobs[jobs.length - 1]?.created_at);
    return oldest !== "" && oldest < quickRange.from;
  }, [jobs, quickRange.from]);

  const visibleJobs = useMemo(() => {
    return jobs.filter((j) => {
      if (showModeTabs && activeModeTab !== "All" && j.mode !== activeModeTab) return false;
      if (customer !== QUICK_ALL && (j.customer_name ?? "") !== customer) return false;
      if (statusFilter !== QUICK_ALL && (j.outer_status ?? j.stage) !== statusFilter) return false;
      if (assignedTo !== QUICK_ALL && assignedToLabel(j) !== assignedTo) return false;
      const d = dayKey(j.created_at);
      if (quickRange.from && d && d < quickRange.from) return false;
      if (quickRange.to && d && d > quickRange.to) return false;
      return true;
    });
  }, [jobs, showModeTabs, activeModeTab, customer, statusFilter, assignedTo, quickRange]);

  const columns: Column<Job>[] = [
    {
      // A placeholder button only, for now — opens a blank tab of its own so a real E-docket
      // feature has somewhere to land later without this column having to change again.
      header: "E-docket",
      render: (j) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            window.open(`/jobs/${j.id}/e-docket`, "_blank", "noopener");
          }}
          title="E-docket"
          className="rounded p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-200"
        >
          🖨️
        </button>
      ),
    },
    {
      header: "Date",
      render: (j) => (
        <span className="text-slate-600 dark:text-slate-300">{when(j.created_at, false)}</span>
      ),
    },
    {
      header: "Job No",
      render: (j) => (
        <span className="font-mono font-medium text-slate-900 dark:text-slate-100">{j.reference}</span>
      ),
    },
    {
      header: "ETA",
      render: (j) => (
        <input
          type="date"
          value={j.eta_date ?? ""}
          disabled={!canEditEta || savingEtaId === j.id}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => handleEtaChange(j, e.target.value)}
          className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 outline-none focus:border-indigo-400 disabled:cursor-default disabled:border-transparent disabled:bg-transparent disabled:px-0 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
        />
      ),
    },
    { header: "Status", render: (j) => stageBadge(j.outer_status ?? j.stage) },
    // Who this job is assigned to. Editable for operator/super_admin/admin — the candidate
    // list is narrowed to operators scoped to THIS job's own mode (a Sea Import job only
    // offers Sea Import operators), same idea as the mode tabs above but per-row instead of
    // a personal filter. Always editable, including after someone is already assigned —
    // picking a different name just reassigns it, nothing to unlock first. Read-only for
    // roles that cannot assign (gk2, manager see the name only, when they see this column at
    // all).
    ...(canAssign || crossTenant
      ? [{
          header: "Assigned To",
          render: (j: Job) => {
            if (!canAssign) {
              return (
                <span className="text-slate-600 dark:text-slate-300">
                  {j.operator_name ?? j.pulled_from_sender ?? "—"}
                </span>
              );
            }
            const eligible = operators.filter(
              (o) => o.role === "operator" && (o.modes ?? []).includes(j.mode ?? ""),
            );
            // The currently-assigned operator might not (any longer) be scoped to this job's
            // mode — still show them in the list so the dropdown reflects reality instead of
            // silently reverting to "Unassigned".
            const current = operators.find((o) => o.id === j.assigned_operator_id);
            const options = current && !eligible.some((o) => o.id === current.id)
              ? [current, ...eligible]
              : eligible;
            return (
              <div>
                <select
                  value={j.assigned_operator_id ?? ""}
                  disabled={savingAssignId === j.id}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => handleAssignChange(j, e.target.value)}
                  className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 outline-none focus:border-indigo-400 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                >
                  <option value="">Unassigned</option>
                  {options.map((o) => (
                    <option key={o.id} value={o.id}>{o.full_name}</option>
                  ))}
                </select>
                {/* A hint for who this job likely belongs to, not a pick of its own - the
                    dropdown above is still the real assignment, and stays "Unassigned" so a
                    manually-created job is still visible to any teammate sharing its
                    template (see list_jobs' own OPERATOR visibility rule). Whoever emailed
                    it in, or failing that whoever actually created it - operator_name
                    already carries the creator (see _operator_name in jobs.py), the
                    dropdown just never showed it. */}
                {!j.assigned_operator_id && (j.pulled_from_sender || j.operator_name) && (
                  <div className="mt-0.5 truncate text-[11px] italic text-slate-400 dark:text-slate-500">
                    {j.pulled_from_sender ? `from ${j.pulled_from_sender}` : `created by ${j.operator_name}`}
                  </div>
                )}
              </div>
            );
          },
        } as Column<Job>]
      : []),
    {
      // The consignee on this job's own documents, and ONLY that — blank rather than the
      // template's name before extraction has run or found one, so an empty cell reads as
      // "not known yet" instead of quietly answering with a different thing (the template).
      header: "Importer/Exporter",
      render: (j) => <span>{j.customer_name ?? "—"}</span>,
    },
    {
      header: "",
      className: "text-right",
      render: (j) => (
        <div className="relative flex justify-end">
          {/* The row itself opens the job, so no Open button. This has to stop the click
              from reaching the row, or the menu would also open the job underneath it. */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setOpenMenuId((id) => (id === j.id ? null : j.id));
            }}
            title="More"
            className="rounded p-1.5 text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
          >
            <span className="flex flex-col items-center gap-0.5 px-1">
              <span className="h-1 w-1 rounded-full bg-current" />
              <span className="h-1 w-1 rounded-full bg-current" />
            </span>
          </button>
          {openMenuId === j.id && (
            <>
              {/* Catches the next click anywhere to close the menu, without also triggering
                  whatever was under it (another row, the page behind it). */}
              <div
                className="fixed inset-0 z-10"
                onClick={(e) => {
                  e.stopPropagation();
                  setOpenMenuId(null);
                }}
              />
              <div
                className="absolute right-0 top-full z-20 mt-1 w-32 rounded-lg border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-900"
                onClick={(e) => e.stopPropagation()}
              >
                <button
                  type="button"
                  disabled={deletingId === j.id}
                  onClick={() => {
                    setOpenMenuId(null);
                    handleDelete(j);
                  }}
                  className="block w-full px-3 py-1.5 text-left text-sm text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:text-rose-400 dark:hover:bg-rose-500/10"
                >
                  {deletingId === j.id ? "Deleting…" : "Delete"}
                </button>
              </div>
            </>
          )}
        </div>
      ),
    },
  ];

  const quickField =
    "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-indigo-400 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

  const filterLabel = group && GROUP_LABELS[group as keyof typeof GROUP_LABELS]
    ? `${GROUP_LABELS[group as keyof typeof GROUP_LABELS]} · ${BUCKETS.find((b) => b.key === bucket)?.label ?? "Overall"}`
    : null;

  return (
    <AppShell
      title="Jobs"
      subtitle="Upload a transaction's documents, extract, and cross-verify."
      actions={
        <div className="flex gap-2">
          {crossTenant && (
            <select
              value={tenantFilter}
              onChange={(e) => setTenantFilter(e.target.value)}
              className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
              title="Show only this tenant's jobs"
            >
              <option value="">All tenants</option>
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          )}
          {user?.role !== "gk2" && user?.role !== "manager" && (
            <>
              <Button size="sm" variant="secondary" onClick={checkEmail} isLoading={checkingMail}>
                ✉ Check Email
              </Button>
              <Button size="sm" onClick={() => setModalOpen(true)} disabled={groups.length === 0}>
                + New Job
              </Button>
            </>
          )}
        </div>
      }
    >
      {error && (
        <div className="mb-6">
          <Alert>{error}</Alert>
        </div>
      )}
      {mailMsg && (
        <div className="mb-6 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm text-indigo-800 dark:border-indigo-500/20 dark:bg-indigo-500/10 dark:text-indigo-300">
          {mailMsg}
        </div>
      )}
      {groups.length === 0 && !loading && (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300">
          No template sets available yet. A Super Admin needs to configure one in “Customer with Template Creation” first.
        </div>
      )}

      {filterLabel && (
        <div className="mb-4 flex items-center justify-between rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-2.5 dark:border-indigo-500/30 dark:bg-indigo-500/10">
          <span className="text-sm font-medium text-indigo-800 dark:text-indigo-200">{filterLabel}</span>
          <button
            type="button"
            onClick={() => setSearchParams({})}
            className="text-xs font-medium text-indigo-700 hover:underline dark:text-indigo-300"
          >
            Clear filter
          </button>
        </div>
      )}

      <Card className="mb-4 p-5">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">Quick filter</label>
            <select
              className={quickField}
              value={quick}
              onChange={(e) => {
                setQuick(e.target.value as typeof quick);
                setDateFrom("");
                setDateTo("");
              }}
            >
              <option value="today">Today</option>
              <option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option>
              <option value="all">All time</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">From</label>
            <input type="date" className={quickField} value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">To</label>
            <input type="date" className={quickField} value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </div>
          <div className="min-w-0 flex-1">
            <label className="mb-1 block text-xs font-medium text-slate-500">Customer</label>
            <select
              className={`${quickField} w-full`}
              value={customer}
              onChange={(e) => setCustomer(e.target.value)}
            >
              <option value={QUICK_ALL}>All customers</option>
              {customers.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>
          <div className="min-w-0 flex-1">
            <label className="mb-1 block text-xs font-medium text-slate-500">Status</label>
            <select
              className={`${quickField} w-full`}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value={QUICK_ALL}>All statuses</option>
              {statuses.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
          <div className="min-w-0 flex-1">
            <label className="mb-1 block text-xs font-medium text-slate-500">Assigned To</label>
            <select
              className={`${quickField} w-full`}
              value={assignedTo}
              onChange={(e) => setAssignedTo(e.target.value)}
            >
              <option value={QUICK_ALL}>All assignees</option>
              {assignees.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </div>
          {(dateFrom || dateTo || customer !== QUICK_ALL || statusFilter !== QUICK_ALL || assignedTo !== QUICK_ALL || quick !== "today") && (
            <button
              type="button"
              onClick={() => {
                setQuick("today");
                setDateFrom("");
                setDateTo("");
                setCustomer(QUICK_ALL);
                setStatusFilter(QUICK_ALL);
                setAssignedTo(QUICK_ALL);
              }}
              className="pb-2 text-xs text-slate-500 hover:underline dark:text-slate-400"
            >
              Reset
            </button>
          )}
        </div>
      </Card>

      {showModeTabs && (
        <div className="mb-4 flex gap-1.5 overflow-x-auto">
          {["All", ...assignedModes].map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setActiveModeTab(mode)}
              className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                activeModeTab === mode
                  ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
                  : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
              }`}
            >
              {mode !== "All" && <ModeIcon mode={mode} />}
              {mode}
            </button>
          ))}
        </div>
      )}

      <Card>
        {!loading && (
          <DataTable
            // Its own browser tab, the same as the Open button did — middle-click and
            // ctrl-click are lost this way, which is the price of losing the button.
            onRowClick={(j) => window.open(`/jobs/${j.id}`, "_blank", "noopener")}
            columns={columns}
            rows={visibleJobs}
            keyFor={(j) => j.id}
            emptyMessage={filterLabel ? "No jobs match this filter." : "No jobs yet — create one to process a transaction."}
          />
        )}
      </Card>
      {/* Just past the table — entering view is what triggers the next batch. */}
      <div ref={sentinelRef} className="flex justify-center py-4">
        {loadingMore && <span className="text-xs text-slate-400">Loading more…</span>}
      </div>

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title="New Job">
        <form className="flex flex-col gap-4" onSubmit={handleCreate}>
          <Select label="Template set (customer)" value={groupId} onChange={(e) => setGroupId(e.target.value)} required>
            {groups.map((g) => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </Select>
          <p className="text-xs text-slate-500">A Job No is generated automatically.</p>
          {error && <Alert>{error}</Alert>}
          <Button type="submit" isLoading={submitting} className="mt-1">Create &amp; open</Button>
        </form>
      </Modal>
    </AppShell>
  );
}
