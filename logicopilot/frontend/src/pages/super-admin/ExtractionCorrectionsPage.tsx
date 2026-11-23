import { Fragment, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { formatDateTime } from "../../utils/dateTime";
import * as correctionsApi from "../../api/extractionCorrections";
import type { CorrectedField, CorrectedLine, CorrectionEdits, CorrectionSummary } from "../../api/extractionCorrections";
import * as tenantsApi from "../../api/tenants";
import type { Tenant } from "../../types/tenant";

const FIELD = "rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900";

const KIND_STYLE: Record<CorrectedLine["kind"], string> = {
  wrong: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300",
  missed: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
  cleared: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
};
const KIND_LABEL: Record<CorrectedLine["kind"], string> = {
  wrong: "Wrong", missed: "Missed", cleared: "Cleared",
};

/** The part both values share at the start and end stays plain; only the middle that differs
 *  is marked - struck through on the AI side, highlighted on the saved side. */
function Diff({ before, after }: { before: string | null; after: string | null }) {
  const a = before ?? "";
  const b = after ?? "";
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let end = 0;
  while (end < a.length - start && end < b.length - start && a[a.length - 1 - end] === b[b.length - 1 - end]) end++;
  const part = (s: string) => [s.slice(0, start), s.slice(start, s.length - end), s.slice(s.length - end)];
  const [ap, am, as] = part(a);
  const [bp, bm, bs] = part(b);
  return (
    <div className="grid gap-1 font-mono text-xs">
      <div className="flex items-baseline gap-2">
        <span className="w-10 shrink-0 font-sans text-[11px] text-slate-400">AI</span>
        <span className="break-all">
          {a ? <>{ap}<del className="rounded bg-rose-100 px-0.5 text-rose-700 dark:bg-rose-500/20 dark:text-rose-300">{am}</del>{as}</>
            : <i className="font-sans text-slate-400">blank</i>}
        </span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="w-10 shrink-0 font-sans text-[11px] text-slate-400">Saved</span>
        <span className="break-all">
          {b ? <>{bp}<ins className="rounded bg-emerald-100 px-0.5 text-emerald-800 no-underline dark:bg-emerald-500/20 dark:text-emerald-300">{bm}</ins>{bs}</>
            : <i className="font-sans text-slate-400">blank</i>}
        </span>
      </div>
    </div>
  );
}

function fieldKey(f: CorrectedField) {
  return `${f.group_id}|${f.template_document_id ?? ""}|${f.label_name}`;
}

export function ExtractionCorrectionsPage() {
  const [minJobs, setMinJobs] = useState(3);
  const [days, setDays] = useState<number | undefined>(undefined);
  const [tenantId, setTenantId] = useState("");
  const [withOperatorFields, setWithOperatorFields] = useState(false);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [summary, setSummary] = useState<CorrectionSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<CorrectedField | null>(null);
  const [edits, setEdits] = useState<CorrectionEdits | null>(null);
  const [editsLoading, setEditsLoading] = useState(false);
  const [openHistory, setOpenHistory] = useState<string | null>(null);

  useEffect(() => {
    tenantsApi.listTenants().then(setTenants).catch(() => setTenants([]));
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    correctionsApi.getSummary({ min_jobs: minJobs, days, tenant_id: tenantId || undefined,
      include_operator_fields: withOperatorFields || undefined })
      .then((s) => { if (live) setSummary(s); })
      .catch(() => { if (live) setError("Could not load the corrections. Try again in a moment."); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [minJobs, days, tenantId, withOperatorFields]);

  useEffect(() => {
    if (!selected) { setEdits(null); return; }
    let live = true;
    setEditsLoading(true);
    correctionsApi.getEdits(selected, days, tenantId || undefined)
      .then((e) => { if (live) setEdits(e); })
      .catch(() => { if (live) setEdits(null); })
      .finally(() => { if (live) setEditsLoading(false); });
    return () => { live = false; };
  }, [selected, days, tenantId]);

  const fields = summary?.fields ?? [];
  const totals = useMemo(() => ({
    fields: fields.length,
    jobs: fields.reduce((n, f) => n + f.jobs_corrected, 0),
  }), [fields]);

  return (
    <AppShell
      title="Extraction Corrections"
      subtitle="Fields operators keep correcting across different jobs: the ones whose extraction prompt needs work."
    >
      <Card className="mb-4 p-4">
        <div className="flex flex-wrap items-end gap-4">
          <label className="grid gap-1 text-xs font-medium text-slate-500">
            Corrected on at least
            <span className="flex items-center gap-2">
              <input id="min-jobs" type="number" min={1} value={minJobs}
                onChange={(e) => setMinJobs(Math.max(1, Number(e.target.value) || 1))}
                className={`${FIELD} w-20`} />
              <span className="text-sm text-slate-600 dark:text-slate-300">different jobs</span>
            </span>
          </label>
          <label className="grid gap-1 text-xs font-medium text-slate-500">
            Period
            <select id="period" value={days ?? ""} onChange={(e) => setDays(e.target.value ? Number(e.target.value) : undefined)} className={FIELD}>
              <option value="">All time</option>
              <option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option>
              <option value="90">Last 90 days</option>
            </select>
          </label>
          <label className="grid gap-1 text-xs font-medium text-slate-500">
            Customer
            <select id="tenant" value={tenantId} onChange={(e) => { setTenantId(e.target.value); setSelected(null); }} className={FIELD}>
              <option value="">All customers</option>
              {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2 self-center text-sm text-slate-600 dark:text-slate-300"
            title="Branch, consignee, insurance and other fields the operator fills or that hold a fixed value - the AI never reads them">
            <input id="with-operator-fields" type="checkbox" checked={withOperatorFields}
              onChange={(e) => { setWithOperatorFields(e.target.checked); setSelected(null); }} />
            Include operator-entry fields
          </label>
          <p className="ml-auto max-w-md text-xs text-slate-500 dark:text-slate-400">
            {summary?.history_since
              ? `Every edit is recorded since ${formatDateTime(summary.history_since)}. Corrections made before that show as "1+" edits.`
              : 'Edit history starts with the next correction. Earlier corrections show as "1+" edits.'}
          </p>
        </div>
      </Card>

      {error && <Card className="mb-4 p-4 text-sm text-rose-600">{error}</Card>}

      <Card className="mb-4">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">Fields corrected on {minJobs}+ jobs</h2>
          <span className="text-xs text-slate-500">{loading ? "Loading…" : `${totals.fields} field(s)`}</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800">
                <th className="px-5 py-2.5 font-medium">Field</th>
                <th className="px-3 py-2.5 font-medium">Template · Document</th>
                <th className="px-3 py-2.5 text-right font-medium">Jobs corrected</th>
                <th className="px-3 py-2.5 text-right font-medium">Edits</th>
                <th className="px-3 py-2.5 text-right font-medium">Rate</th>
                <th className="px-3 py-2.5 font-medium">Wrong / Missed / Cleared</th>
                <th className="px-3 py-2.5 font-medium">Latest AI → Saved</th>
                <th className="px-5 py-2.5 font-medium">Last seen</th>
              </tr>
            </thead>
            <tbody>
              {!loading && fields.length === 0 && (
                <tr><td colSpan={8} className="px-5 py-12 text-center text-sm text-slate-500">
                  No field has been corrected on {minJobs} or more different jobs{days ? ` in the last ${days} days` : ""}.
                </td></tr>
              )}
              {fields.map((f) => {
                const active = selected && fieldKey(selected) === fieldKey(f);
                const ex = f.examples[f.examples.length - 1];
                return (
                  <tr key={fieldKey(f)} onClick={() => { setSelected(active ? null : f); setOpenHistory(null); }}
                    className={`cursor-pointer border-b border-slate-100 align-top last:border-0 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50 ${active ? "bg-indigo-50/60 dark:bg-indigo-500/10" : ""}`}>
                    <td className="px-5 py-3 font-medium text-slate-900 dark:text-slate-100">{f.label_name}
                      {!tenantId && f.tenant && <div className="text-xs font-normal text-slate-400">{f.tenant}</div>}
                    </td>
                    <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{f.template}<div className="text-xs text-slate-400">{f.document}</div></td>
                    <td className="px-3 py-3 text-right font-semibold tabular-nums">{f.jobs_corrected}</td>
                    <td className="px-3 py-3 text-right tabular-nums">{f.total_edits}</td>
                    <td className="px-3 py-3 text-right tabular-nums" title={`${f.jobs_corrected} of ${f.jobs_with_field} jobs with this field`}>
                      {f.correction_rate != null ? `${f.correction_rate}%` : "—"}
                    </td>
                    <td className="px-3 py-3 tabular-nums text-xs">
                      <span className="text-rose-600 dark:text-rose-300">{f.wrong}</span>{" / "}
                      <span className="text-amber-600 dark:text-amber-300">{f.missed}</span>{" / "}
                      <span className="text-slate-500">{f.cleared}</span>
                    </td>
                    <td className="max-w-[18rem] px-3 py-3 font-mono text-xs">
                      {ex && <span className="break-all"><span className="text-slate-400">{ex.ai_value || "blank"}</span> → <span className="text-slate-800 dark:text-slate-100">{ex.final_value || "blank"}</span></span>}
                    </td>
                    <td className="whitespace-nowrap px-5 py-3 text-xs text-slate-500">{formatDateTime(f.last_seen)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {selected && (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-5 py-3 dark:border-slate-800">
            <h2 className="font-semibold text-slate-900 dark:text-slate-50">
              {selected.label_name} <span className="font-normal text-slate-500">· {selected.template} · {selected.document}</span>
            </h2>
            <span className="text-xs text-slate-500">
              {editsLoading ? "Loading…" : edits ? `${edits.total_lines} corrected value(s) on ${edits.jobs} job(s)${edits.total_lines > edits.lines.length ? ` · latest ${edits.lines.length} shown` : ""}` : ""}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800">
                  <th className="px-5 py-2.5 font-medium">Job No</th>
                  <th className="px-3 py-2.5 font-medium">Line</th>
                  <th className="px-3 py-2.5 font-medium">Before → After</th>
                  <th className="px-3 py-2.5 text-right font-medium">Edits</th>
                  <th className="px-3 py-2.5 font-medium">Type</th>
                  <th className="px-5 py-2.5 font-medium">Last edited</th>
                </tr>
              </thead>
              <tbody>
                {(edits?.lines ?? []).map((l) => {
                  const key = `${l.job_id}|${l.row_index ?? ""}|${l.set_index ?? ""}|${l.job_document_id ?? ""}`;
                  const last = l.history[l.history.length - 1];
                  return (
                    <Fragment key={key}>
                      <tr className="border-b border-slate-100 align-top dark:border-slate-800">
                        <td className="whitespace-nowrap px-5 py-3 font-mono text-xs">
                          {l.job_exists
                            ? <Link to={`/jobs/${l.job_id}`} target="_blank" className="text-indigo-600 hover:underline dark:text-indigo-300">{l.job_reference}</Link>
                            : <span className="text-slate-400" title="This job has been deleted">{l.job_reference} (deleted)</span>}
                        </td>
                        <td className="px-3 py-3 text-xs text-slate-500">{l.row_index != null ? `Line ${l.row_index}` : "—"}{l.set_index && l.set_index > 1 ? ` · invoice ${l.set_index}` : ""}</td>
                        <td className="min-w-[16rem] px-3 py-3"><Diff before={l.ai_value} after={l.final_value} /></td>
                        <td className="px-3 py-3 text-right tabular-nums">
                          {l.edit_count != null
                            ? <button type="button" className="text-indigo-600 hover:underline dark:text-indigo-300" onClick={() => setOpenHistory(openHistory === key ? null : key)}>{l.edit_count}</button>
                            : <span className="text-slate-400" title="Corrected before edit history was recorded">1+</span>}
                        </td>
                        <td className="px-3 py-3"><span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${KIND_STYLE[l.kind]}`}>{KIND_LABEL[l.kind]}</span></td>
                        <td className="whitespace-nowrap px-5 py-3 text-xs text-slate-500">
                          {formatDateTime(l.last_at)}{last?.by ? <div>{last.by}</div> : null}
                        </td>
                      </tr>
                      {openHistory === key && (
                        <tr className="border-b border-slate-100 bg-slate-50 dark:border-slate-800 dark:bg-slate-800/40">
                          <td colSpan={6} className="px-5 py-3">
                            <ol className="grid gap-1.5 text-xs">
                              {l.history.map((h, i) => (
                                <li key={i} className="flex flex-wrap items-baseline gap-2">
                                  <span className="w-36 shrink-0 text-slate-500">{formatDateTime(h.at)}</span>
                                  <span className="font-mono break-all"><span className="text-slate-400">{h.before || "blank"}</span> → {h.after || "blank"}</span>
                                  <span className="text-slate-500">{h.by ?? ""}{h.source === "carry" ? " · carried to all lines" : ""}</span>
                                </li>
                              ))}
                            </ol>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </AppShell>
  );
}
