import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import { DataTable, type Column } from "../../components/ui/DataTable";
import { Pagination } from "../../components/ui/Pagination";
import * as dupApi from "../../api/duplicateEmails";

/** "14 Nov, 10:35" - the shape the Jobs list already prints its dates in. */
function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })}, ${d
    .toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
}

type QuickKey = "today" | "yesterday" | "7" | "30" | "this_month" | "prev_month" | "all" | "custom";

/** The same named ranges the Jobs list offers, filling From/To rather than blanking them -
 *  so the window being applied is visible and either end can then be nudged by hand. */
/** "YYYY-MM-DD" in the viewer's own timezone. toISOString() is UTC, which in India puts local
 *  midnight on the previous day - "Last 30 days" ended yesterday and hid today's duplicates. */
function localDayKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function rowDayKey(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : localDayKey(d);
}

function rangeFor(key: QuickKey): { from: string; to: string } {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const iso = localDayKey;
  const back = (days: number) => {
    const d = new Date(today);
    d.setDate(d.getDate() - days);
    return d;
  };
  switch (key) {
    case "today": return { from: iso(today), to: iso(today) };
    case "yesterday": return { from: iso(back(1)), to: iso(back(1)) };
    case "7": return { from: iso(back(6)), to: iso(today) };
    case "30": return { from: iso(back(29)), to: iso(today) };
    case "this_month":
      return { from: iso(new Date(today.getFullYear(), today.getMonth(), 1)), to: iso(today) };
    case "prev_month": {
      const first = new Date(today.getFullYear(), today.getMonth() - 1, 1);
      const last = new Date(today.getFullYear(), today.getMonth(), 0);
      return { from: iso(first), to: iso(last) };
    }
    default: return { from: "", to: "" };
  }
}

const FIELD =
  "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-indigo-400 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

const ALL = "__all__";

/** Pre-alerts whose BL this customer had already sent in.
 *
 *  These never became jobs and never will unless someone says so here: no attachment was
 *  downloaded, no page was OCR'd, no model was called. That is the point - a re-sent
 *  pre-alert costs a full OCR and AI run if it is let through, and leaves the operator two
 *  identical jobs to tell apart.
 *
 *  Two ways out, and only two. Delete, when it really was a re-send. Process as new job,
 *  when it really is a separate shipment - which replays the kept mail through the ordinary
 *  pipeline from the beginning. */
export function DuplicateEmailsPage() {
  const [rows, setRows] = useState<dupApi.DuplicateEmail[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Filters, mirroring the Jobs list so the two screens read the same way.
  const [quick, setQuick] = useState<QuickKey>("30");
  const initial = rangeFor("30");
  const [dateFrom, setDateFrom] = useState(initial.from);
  const [dateTo, setDateTo] = useState(initial.to);
  const [customer, setCustomer] = useState<string>(ALL);
  const [blFilter, setBlFilter] = useState("");
  const [modeTab, setModeTab] = useState<string>(ALL);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const load = useCallback(async () => {
    try {
      setRows(await dupApi.listDuplicateEmails());
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.detail ?? "Could not load the duplicate emails.");
      }
      setRows([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const customers = useMemo(
    () => [...new Set((rows ?? []).map((r) => r.customer).filter(Boolean) as string[])].sort(),
    [rows],
  );
  const modes = useMemo(
    () => [...new Set((rows ?? []).map((r) => r.mode).filter(Boolean) as string[])].sort(),
    [rows],
  );

  /** Everything except the mode tab - so each tab can show its own count against the same
   *  filters, the way the Jobs list's tabs do. */
  const filtered = useMemo(() => {
    const needle = blFilter.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
    return (rows ?? []).filter((r) => {
      if (needle && !r.bl_number.includes(needle)) return false;
      if (customer !== ALL && r.customer !== customer) return false;
      if (dateFrom || dateTo) {
        const day = rowDayKey(r.received_at);
        if (!day) return false;
        if (dateFrom && day < dateFrom) return false;
        if (dateTo && day > dateTo) return false;
      }
      return true;
    });
  }, [rows, blFilter, customer, dateFrom, dateTo]);

  const shown = useMemo(
    () => (modeTab === ALL ? filtered : filtered.filter((r) => (r.mode ?? "") === modeTab)),
    [filtered, modeTab],
  );

  // Any filter change puts you back on page 1 - staying on page 3 of a list that now has
  // one page reads as "nothing here".
  useEffect(() => {
    setPage(1);
  }, [blFilter, customer, dateFrom, dateTo, modeTab, pageSize]);

  const pageRows = useMemo(
    () => shown.slice((page - 1) * pageSize, page * pageSize),
    [shown, page, pageSize],
  );

  function reset() {
    const r = rangeFor("30");
    setQuick("30");
    setDateFrom(r.from);
    setDateTo(r.to);
    setCustomer(ALL);
    setBlFilter("");
    setModeTab(ALL);
  }

  async function remove(row: dupApi.DuplicateEmail) {
    if (!window.confirm(
      `Delete this email?\n\nBL ${row.bl_number} already came in from this sender`
      + `${row.original_reference ? ` and became ${row.original_reference}` : ""}.`
      + `\n\nNo job is created, and the copy of the email is removed. This cannot be undone.`,
    )) return;
    setBusyId(row.id);
    setError(null);
    setNotice(null);
    try {
      await dupApi.deleteDuplicateEmail(row.id);
      setNotice(`Deleted — BL ${row.bl_number} stays on ${row.original_reference ?? "its original job"}.`);
      await load();
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not delete it.");
    } finally {
      setBusyId(null);
    }
  }

  async function process(row: dupApi.DuplicateEmail) {
    if (!window.confirm(
      `Process this as a new job?\n\nBL ${row.bl_number} already came in from this sender`
      + `${row.original_reference ? ` and became ${row.original_reference}` : ""}.`
      + `\n\nUse this only when it really is a separate shipment. The email goes through the`
      + ` normal reading and extraction, which takes a few minutes.`,
    )) return;
    setBusyId(row.id);
    setError(null);
    setNotice(null);
    try {
      const done = await dupApi.processDuplicateEmail(row.id);
      setNotice(`Processing finished — the email became a new job.${
        done.original_reference ? ` ${done.original_reference} is untouched.` : ""}`);
      await load();
    } catch (err) {
      if (axios.isAxiosError(err)) {
        // Reading and extracting a whole mail outlives its own request on a big one - the
        // same cut connection Re-classify hits. It says nothing about the outcome.
        const cut = !err.response || [502, 503, 504].includes(err.response.status);
        setError(cut
          ? "Still working — reading and extracting the email takes a few minutes. "
            + "It is running on the server; refresh this page shortly to see the result."
          : err.response?.data?.detail ?? "Could not process it.");
      }
      await load();
    } finally {
      setBusyId(null);
    }
  }

  // DataTable's render takes the row, not its position, so the row number is looked up
  // rather than counted - it has to match what is actually on screen after filtering.
  const rowNumber = new Map(shown.map((r, i) => [r.id, i + 1]));

  const columns: Column<dupApi.DuplicateEmail>[] = [
    {
      header: "S.No",
      className: "w-14",
      render: (r) => <span className="text-slate-400">{rowNumber.get(r.id)}</span>,
    },
    {
      header: "",
      className: "w-8",
      render: () => (
        <svg className="h-4 w-4 text-indigo-400" fill="none" viewBox="0 0 24 24"
             stroke="currentColor" strokeWidth={2} aria-label="email">
          <rect x="3" y="5" width="18" height="14" rx="2" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M3.5 6.5L12 13l8.5-6.5" />
        </svg>
      ),
    },
    {
      header: "Received Date",
      render: (r) => <span className="whitespace-nowrap text-slate-700 dark:text-slate-200">{when(r.received_at)}</span>,
    },
    {
      header: "Sender / Customer",
      render: (r) => (
        <div className="min-w-0">
          <div className="truncate text-slate-800 dark:text-slate-100">{r.customer ?? "—"}</div>
          <div className="truncate text-[11px] text-slate-400">{r.sender ?? ""}</div>
        </div>
      ),
    },
    {
      header: "BL No",
      render: (r) => <span className="font-mono text-slate-700 dark:text-slate-200">{r.bl_number}</span>,
    },
    {
      header: "Email Subject",
      render: (r) => (
        <span className="block max-w-xs truncate text-slate-600 dark:text-slate-300" title={r.subject ?? ""}>
          {r.subject ?? "—"}
        </span>
      ),
    },
    {
      header: "Original Job No",
      render: (r) =>
        r.original_job_id ? (
          <Link
            to={`/jobs/${r.original_job_id}`}
            className="font-mono text-indigo-600 underline decoration-dotted underline-offset-2 hover:text-indigo-700 dark:text-indigo-400"
          >
            {r.original_reference ?? "open"}
          </Link>
        ) : (
          // The job it duplicated has since been deleted. The mail is still a real thing
          // that arrived, and still needs a decision.
          <span className="text-slate-400" title="That job has since been deleted">
            {r.original_reference ?? "—"}
          </span>
        ),
    },
    {
      header: "Original Received Date",
      render: (r) => <span className="whitespace-nowrap text-slate-500 dark:text-slate-400">{when(r.original_received_at)}</span>,
    },
    {
      header: "Actions",
      render: (r) => (
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={busyId !== null || !r.can_process}
            isLoading={busyId === r.id}
            onClick={() => process(r)}
            title={r.can_process
              ? "Run this email through the normal reading and extraction, as a new job"
              : "The copy of this email is no longer on disk, so it cannot be processed"}
          >
            Process as New Job
          </Button>
          <Button
            size="sm"
            variant="danger"
            disabled={busyId !== null}
            onClick={() => remove(r)}
            title="Delete this email. No job is created."
          >
            Delete
          </Button>
        </div>
      ),
    },
  ];

  const tabCls = (on: boolean) =>
    `flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium ${
      on
        ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
        : "text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
    }`;

  return (
    <AppShell title="Duplicate Emails">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-50">Duplicate Emails</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Emails with the same BL number from the same customer within the last 30 days.
          Nothing has been read out of these — no job, no extraction, no AI — until you decide.
        </p>
      </div>

      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      {notice && (
        <p className="mb-4 rounded-lg bg-emerald-50 px-3.5 py-2.5 text-sm text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
          {notice}
        </p>
      )}

      <Card className="mb-4 p-5">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">Quick filter</label>
            <select
              className={FIELD}
              value={quick}
              onChange={(e) => {
                const next = e.target.value as QuickKey;
                const { from, to } = rangeFor(next);
                setQuick(next);
                setDateFrom(from);
                setDateTo(to);
              }}
            >
              <option value="today">Today</option>
              <option value="yesterday">Yesterday</option>
              <option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option>
              <option value="this_month">This month</option>
              <option value="prev_month">Previous month</option>
              <option value="all">All time</option>
              {quick === "custom" && <option value="custom">Custom range</option>}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">From</label>
            {/* Typing a date of your own stops the dropdown claiming a named range it no
                longer describes - the list filters off these two boxes either way. */}
            <input type="date" className={FIELD} value={dateFrom}
                   onChange={(e) => { setDateFrom(e.target.value); setQuick("custom"); }} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">To</label>
            <input type="date" className={FIELD} value={dateTo}
                   onChange={(e) => { setDateTo(e.target.value); setQuick("custom"); }} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">Customer</label>
            <select className={FIELD} value={customer} onChange={(e) => setCustomer(e.target.value)}>
              <option value={ALL}>All customers</option>
              {customers.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className="min-w-[14rem] flex-1">
            <label className="mb-1 block text-xs font-medium text-slate-500">BL No</label>
            <input
              type="text"
              className={`${FIELD} w-full`}
              placeholder="Search BL number"
              value={blFilter}
              onChange={(e) => setBlFilter(e.target.value)}
            />
          </div>
          <button
            type="button"
            onClick={reset}
            className="pb-2 text-sm text-indigo-600 hover:text-indigo-700 dark:text-indigo-400"
          >
            Reset
          </button>
        </div>
      </Card>

      {modes.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <button type="button" className={tabCls(modeTab === ALL)} onClick={() => setModeTab(ALL)}>
            All
            <span className="rounded-full bg-indigo-100 px-1.5 text-[11px] text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300">
              {filtered.length}
            </span>
          </button>
          {modes.map((m) => (
            <button key={m} type="button" className={tabCls(modeTab === m)} onClick={() => setModeTab(m)}>
              {m}
              <span className="rounded-full bg-slate-100 px-1.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {filtered.filter((r) => r.mode === m).length}
              </span>
            </button>
          ))}
        </div>
      )}

      <Card className="p-4">
        <DataTable
          columns={columns}
          rows={pageRows}
          keyFor={(r) => r.id}
          emptyMessage={
            rows === null
              ? "Loading…"
              : (rows.length === 0
                  ? "No duplicate emails. Every pre-alert that arrived had a BL number nobody had sent before."
                  : "Nothing matches these filters.")
          }
        />
        <div className="mt-4">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={shown.length}
            onPageChange={setPage}
            noun="duplicate emails"
            extra={
              <label className="mr-3 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                Rows per page
                <select
                  value={pageSize}
                  onChange={(e) => setPageSize(Number(e.target.value))}
                  className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-sm text-slate-700 outline-none focus:border-indigo-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                >
                  {[10, 20, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
            }
          />
        </div>
      </Card>
    </AppShell>
  );
}
