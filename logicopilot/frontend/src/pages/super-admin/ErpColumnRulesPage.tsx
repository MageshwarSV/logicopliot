/** Super Admin - ERP Field Configuration (R7 + R2, 2026-11-28; was "ERP Column Rules", P2).
 *  Pick a template and see every ERP Excel column: which document / field feeds it, whether it
 *  is mandatory, the rules that check it, where it lands (file / sheet / column / ERP field)
 *  and whether the mapping works (Configured / Missing / Invalid / Not used).
 *
 *  Mandatory has ONE meaning: the column's rule "required" - the pre-ERP check stops a job
 *  whose mandatory column is empty (backend app/core/erp_rules.py, app/core/erp_field_map.py).
 *  Suggestions come from: ask-operator-required fields, ERP refusals, onboarding's ERP field
 *  list, and (Analyse recent jobs) columns filled on almost every job the ERP accepted. */
import { Fragment, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import * as rulesApi from "../../api/erpRules";
import type { ConfigRow, ErpRule, FieldConfig, MappingStatus, RuleCheckItem, RuleSuggestion, RuleTemplate } from "../../api/erpRules";
import { formatDateTime } from "../../utils/dateTime";

function errText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) && typeof err.response?.data?.detail === "string" ? err.response.data.detail : fallback;
}

const STATUS: Record<MappingStatus, { label: string; cls: string }> = {
  configured: { label: "Configured", cls: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/10 dark:text-emerald-300" },
  missing: { label: "Missing", cls: "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-500/10 dark:text-rose-300" },
  invalid: { label: "Invalid", cls: "bg-amber-50 text-amber-800 ring-amber-600/30 dark:bg-amber-500/10 dark:text-amber-300" },
  not_used: { label: "Not used", cls: "bg-slate-50 text-slate-500 ring-slate-400/20 dark:bg-slate-800 dark:text-slate-400" },
};

type StatusFilter = "all" | MappingStatus | "mandatory" | "suggested" | "refused";

export function ErpColumnRulesPage() {
  const [templates, setTemplates] = useState<RuleTemplate[]>([]);
  const [groupId, setGroupId] = useState("");
  const [cfg, setCfg] = useState<FieldConfig | null>(null);
  const [suggestions, setSuggestions] = useState<RuleSuggestion[]>([]);
  const [draft, setDraft] = useState<Record<string, ErpRule>>({});
  const [dirty, setDirty] = useState(false);
  const [filter, setFilter] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [sheet, setSheet] = useState("");
  const [showUnused, setShowUnused] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [copySource, setCopySource] = useState("");
  const [jobRef, setJobRef] = useState("");
  const [checkResult, setCheckResult] = useState<{ reference: string; items: RuleCheckItem[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    rulesApi.listTemplates().then(setTemplates).catch((err) => setError(errText(err, "Could not load templates.")));
  }, []);

  async function load(id: string) {
    setError(null);
    setCheckResult(null);
    setEditing(null);
    if (!id) {
      setCfg(null);
      return;
    }
    try {
      const [c, cols] = await Promise.all([rulesApi.getFieldConfig(id), rulesApi.getColumns(id)]);
      setCfg(c);
      setSuggestions(cols.suggestions ?? []);
      setDraft(Object.fromEntries(c.rows.filter((r) => r.rule).map((r) => [r.key, { ...r.rule }])));
      setDirty(false);
    } catch (err) {
      setError(errText(err, "Could not load the configuration."));
    }
  }

  useEffect(() => { setNote(null); load(groupId); }, [groupId]);

  const setRule = (key: string, patch: ErpRule) => {
    setDirty(true);
    setDraft((d) => {
      const next = { ...(d[key] ?? {}), ...patch };
      const empty = Object.values(next).every((v) => v === undefined || v === "" || v === false);
      const copy = { ...d };
      if (empty) delete copy[key];
      else copy[key] = next;
      return copy;
    });
  };

  async function run(what: string, fn: () => Promise<string | void>) {
    setBusy(what);
    setError(null);
    setNote(null);
    try {
      const msg = await fn();
      if (msg) setNote(msg);
    } catch (err) {
      setError(errText(err, "Something went wrong."));
    } finally {
      setBusy(null);
    }
  }

  const save = () => run("save", async () => {
    const r = await rulesApi.saveRules(groupId, draft);
    await load(groupId);
    setTemplates(await rulesApi.listTemplates());
    return `Saved ${r.saved} rule(s).`;
  });

  const analyse = () => run("analyse", async () => {
    const r = await rulesApi.analyseJobs(groupId);
    await load(groupId);
    return `Analysed ${r.jobs} job(s) the ERP accepted.`;
  });

  const acceptStrong = () => run("strong", async () => {
    if (!cfg) return;
    if (dirty && !window.confirm("You have unsaved rule changes - they will be lost. Continue?")) return;
    const keys = cfg.rows.filter((r) => r.suggest_mandatory && r.suggest_strength === "strong").map((r) => r.key);
    const r = await rulesApi.setMandatory(groupId, keys, true);
    await load(groupId);
    return `${r.changed} column(s) marked mandatory.`;
  });

  const decide = (s: RuleSuggestion, accept: boolean) => run("sugg", async () => {
    if (accept) await rulesApi.acceptSuggestion(groupId, s.key);
    else await rulesApi.dismissSuggestion(groupId, s.key);
    await load(groupId);
    setTemplates(await rulesApi.listTemplates());
    return accept ? `Rule added to ${s.header}.` : `Suggestion for ${s.header} dismissed.`;
  });

  const copy = () => run("copy", async () => {
    if (!copySource) return;
    const r = await rulesApi.copyFrom(groupId, copySource);
    await load(groupId);
    return `Copied ${r.copied} rule(s)${r.skipped ? `, ${r.skipped} skipped (no such column here)` : ""}.`;
  });

  const check = () => run("check", async () => {
    if (!jobRef.trim()) return;
    setCheckResult(await rulesApi.checkJob(groupId, jobRef.trim()));
  });

  const rows = cfg?.rows ?? [];
  const sheets = useMemo(() => Array.from(new Set(rows.map((r) => r.sheet))), [rows]);
  const isMandatory = (r: ConfigRow) => (draft[r.key] ? !!draft[r.key].required && !draft[r.key].off : r.mandatory);

  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return rows.filter((r) => {
      if (sheet && r.sheet !== sheet) return false;
      if (f && !`${r.sheet} ${r.header} ${r.doc_field} ${r.document} ${r.erp_field}`.toLowerCase().includes(f)) return false;
      if (status === "all") return showUnused || r.status !== "not_used" || isMandatory(r);
      if (status === "mandatory") return isMandatory(r);
      if (status === "suggested") return r.suggest_mandatory && !isMandatory(r);
      if (status === "refused") return r.refused > 0;
      return r.status === status;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, filter, status, sheet, showUnused, draft]);

  const s = cfg?.summary;
  const chip = (key: StatusFilter, label: string, n: number | undefined, tone = "") => (
    <button key={key} type="button" onClick={() => setStatus(status === key ? "all" : key)}
      className={`rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset transition ${status === key ? "bg-indigo-600 text-white ring-indigo-600" : `${tone || "bg-white text-slate-700 ring-slate-200 dark:bg-slate-900 dark:text-slate-200 dark:ring-slate-700"} hover:ring-indigo-300`}`}>
      {label} <span className="tabular-nums">{n ?? 0}</span>
    </button>
  );
  const input = "rounded border border-slate-300 px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-900";

  return (
    <AppShell title="ERP Field Configuration" subtitle="Every ERP Excel column - which document fills it, whether it is mandatory, how it is checked and whether the mapping works.">
      <div className="space-y-4">
        {error && <Alert>{error}</Alert>}
        <Card className="p-4">
          <div className="flex flex-wrap items-center gap-3">
            <select value={groupId} onChange={(e) => setGroupId(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900">
              <option value="">Choose an ERP template…</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>{t.tenant} — {t.name}{t.rules_saved ? ` (${t.rules_saved} rules)` : ""}</option>
              ))}
            </select>
            {cfg && (
              <>
                <span className="text-sm text-slate-600 dark:text-slate-300">ERP Excel file: <b>{cfg.excel_file || "—"}</b></span>
                <button type="button" onClick={analyse} disabled={!!busy} className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium disabled:opacity-50 dark:border-slate-700"
                  title="Measure how often each column had a value on recent jobs the ERP accepted">
                  {busy === "analyse" ? "Analysing…" : "Analyse recent jobs"}
                </button>
                <span className="text-xs text-slate-500">
                  {cfg.analysis.at ? `Last analysed ${formatDateTime(cfg.analysis.at)} · ${cfg.analysis.jobs} jobs` : "Not analysed yet"}
                </span>
              </>
            )}
          </div>
        </Card>

        {cfg && s && (
          <>
            <div className="flex flex-wrap gap-2">
              {chip("all", "All in use", s.total - s.not_used)}
              {chip("configured", "Configured", s.configured, "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/10 dark:text-emerald-300")}
              {chip("missing", "Missing", s.missing, s.missing ? "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-500/10 dark:text-rose-300" : "")}
              {chip("invalid", "Invalid", s.invalid, s.invalid ? "bg-amber-50 text-amber-800 ring-amber-600/30 dark:bg-amber-500/10 dark:text-amber-300" : "")}
              {chip("not_used", "Not used", s.not_used)}
              {chip("mandatory", "Mandatory", s.mandatory)}
              {chip("suggested", "Suggested mandatory", s.suggested)}
              {chip("refused", "Refused by ERP", s.refused, s.refused ? "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-500/10 dark:text-rose-300" : "")}
            </div>

            {s.suggested_strong > 0 && (
              <Card className="flex flex-wrap items-center justify-between gap-3 border-indigo-200 p-4 dark:border-indigo-500/30">
                <div className="text-sm">
                  <b>{s.suggested_strong} column(s)</b> look mandatory - the ERP refused them as empty, the job screen already
                  requires them, or onboarding marks them. Mark them mandatory and a job with one of them empty is stopped
                  before the ERP, with a Fix card for the operator. Auto ERP jobs still get a value filled in.
                </div>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setStatus("suggested")} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium dark:border-slate-700">Review</button>
                  <button type="button" onClick={acceptStrong} disabled={!!busy} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-60">Mark them mandatory</button>
                </div>
              </Card>
            )}

            {suggestions.length > 0 && (
              <Card className="border-amber-200 p-4 dark:border-amber-500/30">
                <h2 className="font-semibold text-slate-900 dark:text-slate-50">Rules suggested from ERP rejections ({suggestions.length})</h2>
                <p className="mb-3 text-xs text-slate-500">The ERP refused these columns on real jobs. Add the rule and the same value is stopped in Cargora next time, before the ERP.</p>
                <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                  {suggestions.map((sg) => (
                    <li key={sg.key} className="flex flex-wrap items-center justify-between gap-3 py-2 text-sm">
                      <div>
                        <p><span className="text-xs text-slate-500">{sg.sheet}</span> <b>{sg.header}</b>{sg.field ? <span className="text-xs text-slate-500"> ← {sg.field}</span> : null}</p>
                        <p className="text-xs text-slate-600 dark:text-slate-300">ERP said: “{sg.erp_text}” · {sg.count}× · last {sg.last_job}</p>
                        <p className="text-xs font-medium text-indigo-700 dark:text-indigo-300">Rule: {sg.rule_text}</p>
                      </div>
                      <div className="flex gap-2">
                        {!sg.rule.needs_list
                          ? <button type="button" disabled={!!busy} onClick={() => decide(sg, true)} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-60">Add rule</button>
                          : <button type="button" onClick={() => { setFilter(sg.header); setEditing(sg.key); }} className="rounded-lg border border-indigo-300 px-3 py-1.5 text-xs font-semibold text-indigo-700 dark:text-indigo-300">Set list on the column</button>}
                        <button type="button" disabled={!!busy} onClick={() => decide(sg, false)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium disabled:opacity-60 dark:border-slate-700">Dismiss</button>
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            <Card className="p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-3">
                  <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Find a column or field" className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900" />
                  <select value={sheet} onChange={(e) => setSheet(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900">
                    <option value="">All sheets</option>
                    {sheets.map((x) => <option key={x} value={x}>{x}</option>)}
                  </select>
                  {status === "all" && (
                    <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={showUnused} onChange={(e) => setShowUnused(e.target.checked)} />Show unused columns</label>
                  )}
                  <span className="text-xs text-slate-500">{shown.length} column(s)</span>
                </div>
                <div className="flex items-center gap-3">
                  {note && <span className="text-sm text-emerald-700 dark:text-emerald-300">{note}</span>}
                  <button type="button" onClick={save} disabled={!!busy || !dirty} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50">
                    {dirty ? "Save changes" : "Saved"}
                  </button>
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr className="border-b border-slate-200 dark:border-slate-700">
                      <th className="py-2 pr-3">S.No</th>
                      <th className="pr-3">Document</th>
                      <th className="pr-3">Document field</th>
                      <th className="pr-3">Mandatory</th>
                      <th className="pr-3">Validation rules</th>
                      <th className="pr-3">ERP Excel file</th>
                      <th className="pr-3">Sheet</th>
                      <th className="pr-3">Column</th>
                      <th className="pr-3">ERP field</th>
                      <th className="pr-3">Status</th>
                      <th className="pr-3 text-right" title="ERP refusals, last 90 days">Refused</th>
                      <th className="text-right" title="Had a value on this % of recent jobs the ERP accepted">Filled</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r, i) => {
                      const d = draft[r.key] ?? {};
                      const mand = isMandatory(r);
                      return (
                        <Fragment key={r.key}>
                          <tr className="border-t border-slate-100 align-top dark:border-slate-800">
                            <td className="py-2 pr-3 text-xs text-slate-400 tabular-nums">{i + 1}</td>
                            <td className="pr-3 text-xs">{r.document || <span className="text-slate-400">—</span>}{r.per_line && <span className="ml-1 rounded bg-slate-100 px-1 text-[10px] text-slate-500 dark:bg-slate-800">per line</span>}</td>
                            <td className="pr-3 text-xs font-medium">{r.doc_field || <span className="text-slate-400">—</span>}</td>
                            <td className="pr-3">
                              <label className="flex items-center gap-1.5">
                                <input type="checkbox" checked={mand} onChange={(e) => setRule(r.key, { required: e.target.checked, off: false })} />
                                <span className={`text-xs ${mand ? "font-semibold text-rose-700 dark:text-rose-300" : "text-slate-500"}`}>{mand ? "Yes" : "No"}</span>
                              </label>
                              {r.suggest_mandatory && !mand && (
                                <span title={r.mandatory_reasons.join("\n")}
                                  className={`mt-1 inline-block cursor-help rounded px-1.5 py-0.5 text-[10px] font-semibold ${r.suggest_strength === "strong" ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"}`}>
                                  Suggested{r.suggest_strength === "data" ? " (data)" : ""}
                                </span>
                              )}
                            </td>
                            <td className="max-w-[16rem] pr-3 text-xs">
                              {r.rules.length ? r.rules.map((t, n) => <div key={n} className="text-slate-700 dark:text-slate-200">{t}</div>) : <span className="text-slate-400">None</span>}
                              {draft[r.key] && JSON.stringify(draft[r.key]) !== JSON.stringify(r.rule ?? {}) && <div className="text-indigo-700 dark:text-indigo-300">Changed - save to apply</div>}
                              <button type="button" onClick={() => setEditing(editing === r.key ? null : r.key)} className="mt-0.5 text-[11px] font-semibold text-indigo-600 hover:underline dark:text-indigo-400">
                                {editing === r.key ? "Close" : "Edit rules"}
                              </button>
                            </td>
                            <td className="pr-3 text-xs text-slate-500">{r.excel_file}</td>
                            <td className="pr-3 text-xs">{r.sheet}</td>
                            <td className="pr-3 text-xs"><span className="text-slate-400">{r.column}</span> {r.header}</td>
                            <td className="pr-3 text-xs">{r.erp_field}</td>
                            <td className="pr-3">
                              <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${STATUS[r.status].cls}`}>{STATUS[r.status].label}</span>
                              {r.problems.map((p, n) => <div key={n} className="mt-1 max-w-[16rem] text-[11px] text-amber-700 dark:text-amber-300">⚠ {p}</div>)}
                            </td>
                            <td className="pr-3 text-right text-xs tabular-nums">
                              {r.refused ? <span className="font-semibold text-rose-700 dark:text-rose-300" title={r.refused_last ? `Last: ${r.refused_last}` : ""}>{r.refused}×</span> : <span className="text-slate-300">—</span>}
                            </td>
                            <td className="text-right text-xs tabular-nums">
                              {r.fill_pct == null ? <span className="text-slate-300">—</span>
                                : <span className={r.fill_pct < 50 && r.status === "configured" ? "text-amber-700 dark:text-amber-300" : ""}>{r.fill_pct}%</span>}
                            </td>
                          </tr>
                          {editing === r.key && (
                            <tr className="bg-slate-50 dark:bg-slate-900/60">
                              <td />
                              <td colSpan={11} className="px-2 py-3">
                                <div className="flex flex-wrap items-end gap-4 text-xs">
                                  <label className="flex flex-col gap-1">Length
                                    <span className="flex gap-1">
                                      <select value={d.length_mode ?? "max"} onChange={(e) => setRule(r.key, { length_mode: e.target.value as "exact" | "max" })} className={input}>
                                        <option value="max">at most</option><option value="exact">exactly</option>
                                      </select>
                                      <input type="number" min={1} value={d.length ?? ""} onChange={(e) => setRule(r.key, { length: e.target.value ? Number(e.target.value) : undefined })} className={`${input} w-16`} />
                                    </span>
                                  </label>
                                  <label className="flex flex-col gap-1">From list
                                    <select value={d.master ?? ""} onChange={(e) => setRule(r.key, { master: (e.target.value || undefined) as ErpRule["master"] })} className={input}>
                                      <option value="">—</option><option value="country">Country code</option><option value="currency">Currency code</option><option value="unit">Unit (UQC)</option>
                                    </select>
                                  </label>
                                  <label className="flex items-center gap-1.5"><input type="checkbox" checked={!!d.number} onChange={(e) => setRule(r.key, { number: e.target.checked })} />Number</label>
                                  <label className="flex items-center gap-1.5"><input type="checkbox" checked={!!d.uppercase} onChange={(e) => setRule(r.key, { uppercase: e.target.checked })} />Uppercase</label>
                                  <label className="flex flex-col gap-1">Pattern (regex)
                                    <input value={d.pattern ?? ""} onChange={(e) => setRule(r.key, { pattern: e.target.value || undefined })} className={`${input} w-40`} />
                                  </label>
                                  {d.pattern && (
                                    <label className="flex flex-col gap-1">Message for the operator
                                      <input value={d.message ?? ""} onChange={(e) => setRule(r.key, { message: e.target.value || undefined })} className={`${input} w-56`} />
                                    </label>
                                  )}
                                  <label className="flex flex-col gap-1">Allowed values (comma separated)
                                    <input key={(d.allowed ?? []).join(",")} defaultValue={(d.allowed ?? []).join(", ")}
                                      onBlur={(e) => {
                                        const list = e.target.value.split(",").map((x) => x.trim().toUpperCase()).filter(Boolean);
                                        if (list.join(",") !== (d.allowed ?? []).join(",")) setRule(r.key, { allowed: list.length ? list : undefined });
                                      }}
                                      className={`${input} w-56`} placeholder="e.g. PKG, PLT, CTN" />
                                    {r.accepted_values && r.accepted_values.length > 0 && (
                                      <button type="button" onClick={() => setRule(r.key, { allowed: r.accepted_values ?? undefined })}
                                        className="self-start text-[11px] font-semibold text-indigo-600 hover:underline dark:text-indigo-400"
                                        title={r.accepted_values.join(", ")}>
                                        Use the {r.accepted_values.length} value(s) the ERP accepted
                                      </button>
                                    )}
                                  </label>
                                  <label className="flex items-center gap-1.5" title="No check at all on this column (also turns off a built-in country/currency check)">
                                    <input type="checkbox" checked={!!d.off} onChange={(e) => setRule(r.key, { off: e.target.checked })} />Off
                                  </label>
                                </div>
                                {r.mandatory_reasons.length > 0 && (
                                  <p className="mt-2 text-xs text-slate-500">Why it may be mandatory: {r.mandatory_reasons.join(" · ")}</p>
                                )}
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

            <div className="grid gap-4 md:grid-cols-2">
              <Card className="p-4">
                <h2 className="mb-2 font-semibold text-slate-900 dark:text-slate-50">Try the saved rules on a job</h2>
                <div className="flex gap-2">
                  <input value={jobRef} onChange={(e) => setJobRef(e.target.value)} placeholder="JOB-…" className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900" />
                  <button type="button" onClick={check} disabled={!!busy} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium dark:border-slate-700">Check</button>
                </div>
                {checkResult && (checkResult.items.length === 0 ? (
                  <p className="mt-2 text-sm text-emerald-700 dark:text-emerald-300">✓ {checkResult.reference} passes every rule - it would go to the ERP.</p>
                ) : (
                  <ul className="mt-2 space-y-1 text-sm">
                    {checkResult.items.map((it, n) => (
                      <li key={n} className="text-rose-700 dark:text-rose-300">
                        <b>{it.display}</b>{it.where ? ` (${it.where})` : ""}: sent {it.sent.map((x) => `"${x}"`).join(", ") || "(empty)"} — {it.needs}
                      </li>
                    ))}
                  </ul>
                ))}
              </Card>
              <Card className="p-4">
                <h2 className="mb-2 font-semibold text-slate-900 dark:text-slate-50">Copy rules from another template</h2>
                <div className="flex gap-2">
                  <select value={copySource} onChange={(e) => setCopySource(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900">
                    <option value="">Choose a template…</option>
                    {templates.filter((t) => t.id !== groupId && t.rules_saved).map((t) => (
                      <option key={t.id} value={t.id}>{t.tenant} — {t.name} ({t.rules_saved})</option>
                    ))}
                  </select>
                  <button type="button" onClick={copy} disabled={!copySource || !!busy} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium disabled:opacity-50 dark:border-slate-700">Copy</button>
                </div>
                <p className="mt-2 text-xs text-slate-500">Same ERP, same columns: rules are copied for columns this template also has.</p>
              </Card>
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
