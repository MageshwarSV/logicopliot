import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { DataTable, type Column } from "../../components/ui/DataTable";
import * as emailApi from "../../api/email";
import { formatDateTime } from "../../utils/dateTime";

function when(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value.endsWith("Z") || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  if (Number.isNaN(d.getTime())) return "—";
  return formatDateTime(d) || "—";
}

/** Super Admin → Email Puller: the mails that were tried and could not be read because the AI
 *  service refused the call (no credits, a rate limit, an outage), and every mail that arrived
 *  while the puller was holding off for that reason. None of them has a job yet.
 *
 *  They are retried on their own when the wait ends. "Rerun all" is for after the cause has
 *  been fixed - credits added - so nobody has to sit out the rest of the wait. Each mail then
 *  becomes a job in the usual way; one that fails again simply comes back onto this list. */
export function HeldEmailsCard() {
  const [data, setData] = useState<emailApi.HeldEmails | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [rerunning, setRerunning] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await emailApi.listHeldEmails());
      setError(null);
    } catch (err) {
      setError(
        axios.isAxiosError(err)
          ? err.response?.data?.detail ?? "Could not load the held emails."
          : "Could not load the held emails.",
      );
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30000);
    return () => window.clearInterval(timer);
  }, [load]);

  const rerun = async () => {
    setRerunning(true);
    setNotice(null);
    try {
      const { queued } = await emailApi.rerunHeldEmails();
      setNotice(
        queued
          ? `Re-running ${queued} email${queued === 1 ? "" : "s"} now. Each one becomes a job as it is read — this list empties as they go through.`
          : "Checking the mailboxes again now.",
      );
      window.setTimeout(() => void load(), 4000);
    } catch (err) {
      setError(
        axios.isAxiosError(err)
          ? err.response?.data?.detail ?? "Could not start the re-run."
          : "Could not start the re-run.",
      );
    } finally {
      setRerunning(false);
    }
  };

  const columns: Column<emailApi.HeldEmail>[] = [
    { header: "First failed", render: (row) => when(row.first_failed_at), className: "whitespace-nowrap" },
    { header: "Last tried", render: (row) => when(row.last_tried_at), className: "whitespace-nowrap" },
    { header: "From", render: (row) => row.sender ?? "—" },
    { header: "Subject", render: (row) => row.subject ?? "—" },
    { header: "Template", render: (row) => row.template ?? "—" },
    {
      header: "Why",
      render: (row) => (
        <span title={row.reason ?? ""} className="block max-w-md truncate text-xs text-slate-500 dark:text-slate-400">
          {row.reason ?? "—"}
        </span>
      ),
    },
  ];

  const total = data?.total ?? 0;
  return (
    <Card className="mt-6">
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 pt-5">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">
            Emails tried and failed{total ? ` (${total})` : ""}
          </h3>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Mail that could not be read because the AI service was unavailable. No job exists for
            these yet, and nothing is billed for them. They are retried automatically.
          </p>
        </div>
        <Button onClick={rerun} isLoading={rerunning} disabled={!data || (total === 0 && !data.waiting)}>
          Rerun all
        </Button>
      </div>
      {data?.waiting && (
        <p className="mx-5 mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300">
          The email puller is holding off until {when(data.until)} and is not opening any mail.
          {data.reason ? ` Last error: ${data.reason}` : ""} Fix the cause, then press Rerun all.
        </p>
      )}
      {notice && (
        <p className="mx-5 mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
          {notice}
        </p>
      )}
      {error && (
        <p className="mx-5 mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </p>
      )}
      <div className="mt-3">
        <DataTable
          columns={columns}
          rows={data?.items ?? []}
          keyFor={(row) => row.id}
          emptyMessage={data ? "No email is held up." : "Loading…"}
        />
      </div>
    </Card>
  );
}
