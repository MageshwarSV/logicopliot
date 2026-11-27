/** Failed / delayed jobs for Production Support - shared by the Overview and the full list.
 *  Investigate opens the job (read-only for Support); Retry is shown only where the server
 *  allows it (a failed ERP entry, or an extraction that did not finish). */
import { useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import * as supportApi from "../../api/support";
import type { IssueTab, SupportIssue, SupportIssues } from "../../api/support";

const TAB_LABEL: Record<IssueTab, string> = { all: "All", extraction: "Extraction", erp: "ERP Submission", other: "Other" };

export function formatAge(min: number | null): string {
  if (min == null) return "—";
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  if (h < 48) return `${h} h ${min % 60} min`;
  return `${Math.floor(h / 24)} days`;
}

function stageClass(kind: SupportIssue["kind"]): string {
  return kind === "erp_failed" || kind === "erp_slow"
    ? "bg-rose-50 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300"
    : kind === "missing_documents"
      ? "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
      : "bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300";
}

export function IssuesTable({
  data,
  tab,
  onTab,
  onChanged,
  limit,
}: {
  data: SupportIssues | null;
  tab: IssueTab;
  onTab: (t: IssueTab) => void;
  onChanged: () => void;
  limit?: number;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const rows = (data?.items ?? []).slice(0, limit ?? undefined);

  async function handled(issue: SupportIssue) {
    const note = window.prompt(`Mark ${issue.reference} as handled. What was done? (optional)`, "");
    if (note === null) return;
    setBusy(issue.job_id);
    setMessage(null);
    try {
      await supportApi.markHandled(issue.job_id, note);
      setMessage({ ok: true, text: `${issue.reference}: marked handled. It comes back here if it fails again.` });
      onChanged();
    } catch (err) {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null;
      setMessage({ ok: false, text: `${issue.reference}: ${typeof detail === "string" ? detail : "could not mark it handled."}` });
    } finally {
      setBusy(null);
    }
  }

  async function retry(issue: SupportIssue, what: "erp" | "extraction") {
    const label = what === "erp" ? "ERP entry" : "extraction";
    if (!window.confirm(`Retry the ${label} for ${issue.reference} (${issue.client})?`)) return;
    setBusy(issue.job_id);
    setMessage(null);
    try {
      if (what === "erp") await supportApi.retryErp(issue.job_id);
      else await supportApi.retryExtraction(issue.job_id);
      setMessage({ ok: true, text: `${issue.reference}: ${label} restarted. It will drop off this list once it succeeds.` });
      onChanged();
    } catch (err) {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null;
      setMessage({ ok: false, text: `${issue.reference}: ${typeof detail === "string" ? detail : `could not retry the ${label}.`}` });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        {(Object.keys(TAB_LABEL) as IssueTab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => onTab(t)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
              tab === t
                ? "bg-indigo-600 text-white"
                : "border border-slate-200 text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
            }`}
          >
            {TAB_LABEL[t]} ({data?.counts?.[t] ?? 0})
          </button>
        ))}
      </div>
      {message && (
        <p className={`mb-3 rounded-lg px-3 py-2 text-sm ${message.ok ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-rose-50 text-rose-800 dark:bg-rose-500/10 dark:text-rose-300"}`}>
          {message.text}
        </p>
      )}
      <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
            <tr>
              <th className="px-3 py-2 font-semibold">Job No.</th>
              <th className="px-3 py-2 font-semibold">Client</th>
              <th className="px-3 py-2 font-semibold">Type</th>
              <th className="px-3 py-2 font-semibold">Stage</th>
              <th className="px-3 py-2 text-right font-semibold">Pages</th>
              <th className="px-3 py-2 font-semibold">Issue / Reason</th>
              <th className="px-3 py-2 font-semibold">Age</th>
              <th className="px-3 py-2 text-right font-semibold">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-slate-500">
                  {data ? "Nothing needs attention here." : "Loading…"}
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr key={r.job_id} className="align-top">
                <td className="px-3 py-2 font-semibold text-indigo-700 dark:text-indigo-300">
                  <Link to={`/jobs/${r.job_id}`} className="hover:underline">{r.reference}</Link>
                </td>
                <td className="px-3 py-2">{r.client}</td>
                <td className="px-3 py-2 whitespace-nowrap">
                  {r.type && <span className="rounded-full bg-sky-50 px-2 py-0.5 text-xs font-medium text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">{r.type}</span>}
                </td>
                <td className="px-3 py-2 whitespace-nowrap">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${stageClass(r.kind)}`}>{r.stage}</span>
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{r.pages}</td>
                <td className="max-w-md px-3 py-2 text-slate-700 dark:text-slate-300">
                  {r.reason}
                  {r.handled && (
                    <span className="mt-1 block text-xs text-emerald-700 dark:text-emerald-300">✓ Handled{r.handled_note ? ` — ${r.handled_note}` : ""}</span>
                  )}
                </td>
                <td className="px-3 py-2 whitespace-nowrap font-medium text-rose-600 dark:text-rose-400">{formatAge(r.age_minutes)}</td>
                <td className="px-3 py-2">
                  <div className="flex justify-end gap-2">
                    {r.can_retry_erp && (
                      <button type="button" disabled={busy === r.job_id} onClick={() => retry(r, "erp")}
                        className="rounded-lg bg-indigo-600 px-3 py-1 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-60">
                        Retry ERP
                      </button>
                    )}
                    {r.can_retry_extraction && (
                      <button type="button" disabled={busy === r.job_id} onClick={() => retry(r, "extraction")}
                        className="rounded-lg bg-indigo-600 px-3 py-1 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-60">
                        Retry extraction
                      </button>
                    )}
                    {!r.handled && (
                      <button type="button" disabled={busy === r.job_id} onClick={() => handled(r)}
                        className="rounded-lg border border-emerald-300 px-3 py-1 text-xs font-semibold text-emerald-700 hover:bg-emerald-50 disabled:opacity-60 dark:border-emerald-500/40 dark:text-emerald-300 dark:hover:bg-emerald-500/10">
                        Mark handled
                      </button>
                    )}
                    <Link to={`/jobs/${r.job_id}`}
                      className="rounded-lg border border-indigo-200 px-3 py-1 text-xs font-semibold text-indigo-700 hover:bg-indigo-50 dark:border-indigo-500/40 dark:text-indigo-300 dark:hover:bg-indigo-500/10">
                      Investigate
                    </Link>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
