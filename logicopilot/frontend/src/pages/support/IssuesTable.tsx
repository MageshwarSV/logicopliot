/** Failed / delayed jobs for Production Support - shared by the Overview and the full list.
 *  Investigate opens the job (read-only for Support); Retry only where the server allows it
 *  (a failed ERP entry, or an extraction that did not finish); Mark handled with a note. */
import { useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import * as supportApi from "../../api/support";
import type { IssueTab, SupportIssue, SupportIssues } from "../../api/support";
import { ArrowPathIcon, CheckCircleIcon, EyeIcon } from "../../components/ui/icons";
import { Badge, EmptyRow, KIND_TONE, Pager, ROW_EDGE, T, TypeCell, formatWhen, usePaged } from "./supportUi";

/** A long ERP message reads as two lines, with "more" to open it. */
function ReasonText({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 110;
  return (
    <span>
      <span className={open || !long ? "" : "line-clamp-2"}>{text}</span>
      {long && (
        <button type="button" onClick={() => setOpen((v) => !v)} className="mt-0.5 text-xs font-medium text-indigo-700 hover:underline dark:text-indigo-300">
          {open ? "less" : "more"}
        </button>
      )}
    </span>
  );
}

const TAB_LABEL: Record<IssueTab, string> = { all: "All", extraction: "Extraction", erp: "ERP Submission", other: "Other" };

export function formatAge(min: number | null): string {
  if (min == null) return "—";
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  if (h < 48) return `${h} h ${min % 60} min`;
  return `${Math.floor(h / 24)} days`;
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
  const all = data?.items ?? [];
  const paged = usePaged(limit ? all.slice(0, limit) : all, tab);

  async function run(issue: SupportIssue, what: "erp" | "extraction" | "handled") {
    let note = "";
    if (what === "handled") {
      const typed = window.prompt(`Mark ${issue.reference} as handled. What was done? (optional)`, "");
      if (typed === null) return;
      note = typed;
    } else if (!window.confirm(`Retry the ${what === "erp" ? "ERP entry" : "extraction"} for ${issue.reference} (${issue.client})?`)) {
      return;
    }
    setBusy(issue.job_id);
    setMessage(null);
    try {
      if (what === "erp") await supportApi.retryErp(issue.job_id);
      else if (what === "extraction") await supportApi.retryExtraction(issue.job_id);
      else await supportApi.markHandled(issue.job_id, note);
      setMessage({
        ok: true,
        text: what === "handled"
          ? `${issue.reference}: marked handled. It comes back here if it fails again.`
          : `${issue.reference}: ${what === "erp" ? "ERP entry" : "extraction"} restarted. It drops off this list once it succeeds.`,
      });
      onChanged();
    } catch (err) {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null;
      setMessage({ ok: false, text: `${issue.reference}: ${typeof detail === "string" ? detail : "that did not work."}` });
    } finally {
      setBusy(null);
    }
  }

  const btn = "inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-semibold disabled:opacity-50";
  return (
    <div>
      <div className="flex flex-wrap gap-1 border-b border-slate-100 px-4 pt-3 dark:border-slate-800">
        {(Object.keys(TAB_LABEL) as IssueTab[]).map((t) => (
          <button key={t} type="button" onClick={() => onTab(t)}
            className={`-mb-px border-b-2 px-3 pb-2.5 pt-1 text-sm font-medium transition-colors ${tab === t
              ? "border-indigo-600 text-indigo-700 dark:text-indigo-300"
              : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}`}>
            {TAB_LABEL[t]}
            <span className={`ml-2 rounded-full px-2 py-0.5 text-xs font-semibold ${tab === t ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"}`}>
              {data?.counts?.[t] ?? 0}
            </span>
          </button>
        ))}
      </div>
      {message && (
        <p className={`mx-4 mt-3 rounded-lg px-3 py-2 text-sm ${message.ok ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-rose-50 text-rose-800 dark:bg-rose-500/10 dark:text-rose-300"}`}>
          {message.text}
        </p>
      )}
      <div className={T.wrap}>
        <table className={T.table}>
          <thead className={T.thead}>
            <tr>
              <th className={T.th}>Job No.</th><th className={T.th}>Client</th><th className={T.th}>Type</th>
              <th className={T.th}>Problem</th><th className={T.th}>Arrived</th><th className={T.th}>First sent to ERP</th>
              <th className={T.th}>Delay</th><th className={T.th}>Reason</th><th className={T.thRight}>Action</th>
            </tr>
          </thead>
          <tbody>
            {paged.shown.length === 0 && <EmptyRow cols={8} loading={!data} text="✓ Nothing needs attention here." />}
            {paged.shown.map((r) => {
              const tone = KIND_TONE[r.kind] ?? "slate";
              return (
                <tr key={r.job_id} className={`${T.row} ${ROW_EDGE[r.handled ? "green" : tone]} ${tone === "red" && !r.handled ? "bg-rose-50/40 dark:bg-rose-500/5" : ""}`}>
                  <td className={`${T.td} whitespace-nowrap`}><Link to={`/jobs/${r.job_id}`} className={T.link}>{r.reference}</Link></td>
                  <td className={T.td}>{r.client}</td>
                  <td className={T.td}><TypeCell type={r.type} /></td>
                  <td className={T.td}><Badge tone={tone}>{r.kind === "erp_slow" || r.kind === "extraction_slow" ? `${r.stage} · slow` : r.kind === "missing_documents" ? "Waiting for docs" : `${r.stage} · failed`}</Badge></td>
                  <td className={`${T.td} whitespace-nowrap text-slate-600 dark:text-slate-300`}>{formatWhen(r.created_at)}</td>
                  <td className={`${T.td} whitespace-nowrap text-slate-600 dark:text-slate-300`}>
                    {r.first_erp_at ? formatWhen(r.first_erp_at) : <span className="text-slate-400">Not sent yet</span>}
                    {r.first_erp_at && r.last_erp_at && r.last_erp_at !== r.first_erp_at && (
                      <span className="block text-xs text-slate-500">last try {formatWhen(r.last_erp_at)}</span>
                    )}
                  </td>
                  <td className={`${T.td} whitespace-nowrap`}>
                    <span className={`font-semibold ${tone === "red" ? "text-rose-600 dark:text-rose-400" : "text-amber-700 dark:text-amber-300"}`}>
                      {formatAge(r.since_first_erp_minutes ?? r.age_minutes)}
                    </span>
                    <span className="block text-xs text-slate-500">{r.since_first_erp_minutes != null ? "since first sent" : "since it got stuck"}</span>
                  </td>
                  <td className={`${T.td} min-w-[16rem] max-w-md text-slate-700 dark:text-slate-300`}>
                    <ReasonText text={r.reason} />
                    {r.handled && (
                      <span className="mt-1 flex items-center gap-1 text-xs font-medium text-emerald-700 dark:text-emerald-300">
                        <CheckCircleIcon className="h-3.5 w-3.5" />Handled{r.handled_note ? ` — ${r.handled_note}` : ""}
                      </span>
                    )}
                  </td>
                  <td className={T.td}>
                    <div className="flex justify-end gap-1.5">
                      {r.can_retry_erp && (
                        <button type="button" disabled={busy === r.job_id} onClick={() => run(r, "erp")} className={`${btn} bg-indigo-600 text-white hover:bg-indigo-500`}>
                          <ArrowPathIcon className="h-3.5 w-3.5" />Retry ERP
                        </button>
                      )}
                      {r.can_retry_extraction && (
                        <button type="button" disabled={busy === r.job_id} onClick={() => run(r, "extraction")} className={`${btn} bg-indigo-600 text-white hover:bg-indigo-500`}>
                          <ArrowPathIcon className="h-3.5 w-3.5" />Retry extraction
                        </button>
                      )}
                      {!r.handled && (
                        <button type="button" disabled={busy === r.job_id} onClick={() => run(r, "handled")}
                          className={`${btn} border border-emerald-300 text-emerald-700 hover:bg-emerald-50 dark:border-emerald-500/40 dark:text-emerald-300 dark:hover:bg-emerald-500/10`}>
                          <CheckCircleIcon className="h-3.5 w-3.5" />Handled
                        </button>
                      )}
                      <Link to={`/jobs/${r.job_id}`}
                        className={`${btn} border border-slate-200 text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800`}>
                        <EyeIcon className="h-3.5 w-3.5" />Investigate
                      </Link>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!limit && <Pager {...paged} />}
      {limit && all.length > limit && (
        <p className="border-t border-slate-100 px-4 py-2.5 text-xs text-slate-500 dark:border-slate-800">
          Showing the newest {limit} of {all.length} - open Failed / Delayed Jobs for all.
        </p>
      )}
    </div>
  );
}
