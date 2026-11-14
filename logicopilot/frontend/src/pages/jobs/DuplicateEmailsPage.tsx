import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { DataTable, type Column } from "../../components/ui/DataTable";
import * as dupApi from "../../api/duplicateEmails";

/** "14 Nov, 10:35" - the same shape the Jobs list prints its dates in. */
function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })}, ${d
    .toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
}

/** Pre-alerts whose BL this customer had already sent in.
 *
 *  These never became jobs and never will unless someone says so here: no attachment was
 *  downloaded, no page was OCR'd, no model was called. That is the point - a re-sent
 *  pre-alert costs a full OCR and AI run if it is allowed through, and leaves the operator
 *  two identical jobs to tell apart.
 *
 *  Two ways out, and only two. Delete, when it really was a re-send. Process as new job,
 *  when it really is a separate shipment - which runs the kept message through the ordinary
 *  pipeline from the beginning. */
export function DuplicateEmailsPage() {
  const [rows, setRows] = useState<dupApi.DuplicateEmail[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [blFilter, setBlFilter] = useState("");

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

  const shown = useMemo(() => {
    const needle = blFilter.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
    if (!rows) return [];
    if (!needle) return rows;
    return rows.filter((r) => r.bl_number.includes(needle));
  }, [rows, blFilter]);

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
      header: "Received",
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
        <span className="block max-w-sm truncate text-slate-600 dark:text-slate-300" title={r.subject ?? ""}>
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
      header: "Original Received",
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

  return (
    <AppShell title="Duplicate Emails">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-50">Duplicate Emails</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Pre-alerts whose BL number already came in from the same sender in the last 30 days.
          Nothing has been read out of these — no job, no extraction, no AI — until you decide.
        </p>
      </div>

      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      {notice && (
        <p className="mb-4 rounded-lg bg-emerald-50 px-3.5 py-2.5 text-sm text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
          {notice}
        </p>
      )}

      <Card className="p-4">
        <div className="mb-4 w-64">
          <Input
            label="BL No"
            placeholder="Search BL number"
            value={blFilter}
            onChange={(e) => setBlFilter(e.target.value)}
          />
        </div>
        <DataTable
          columns={columns}
          rows={shown}
          keyFor={(r) => r.id}
          emptyMessage={
            rows === null
              ? "Loading…"
              : "No duplicate emails. Every pre-alert that arrived had a BL number nobody had sent before."
          }
        />
      </Card>
    </AppShell>
  );
}
