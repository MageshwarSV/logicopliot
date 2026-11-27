/** Super Admin - ERP Column Rules (P2). What each ERP Excel column accepts, per template.
 *  Checked before the workbook goes to the ERP (backend app/core/erp_rules.py): a value the ERP
 *  would refuse stops the job with the Fix card instead of reaching the ERP. Country-code
 *  columns are checked without any setup; a saved rule replaces that, "Off" removes it. */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import * as rulesApi from "../../api/erpRules";
import type { ErpRule, RuleCheckItem, RuleColumn, RuleSuggestion, RuleTemplate } from "../../api/erpRules";

function errText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) && typeof err.response?.data?.detail === "string" ? err.response.data.detail : fallback;
}

export function ErpColumnRulesPage() {
  const [templates, setTemplates] = useState<RuleTemplate[]>([]);
  const [groupId, setGroupId] = useState("");
  const [columns, setColumns] = useState<RuleColumn[]>([]);
  const [suggestions, setSuggestions] = useState<RuleSuggestion[]>([]);
  const [masters, setMasters] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, ErpRule>>({});
  const [filter, setFilter] = useState("");
  const [onlyRules, setOnlyRules] = useState(false);
  const [copySource, setCopySource] = useState("");
  const [jobRef, setJobRef] = useState("");
  const [checkResult, setCheckResult] = useState<{ reference: string; items: RuleCheckItem[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    rulesApi.listTemplates().then(setTemplates).catch((err) => setError(errText(err, "Could not load templates.")));
  }, []);

  async function loadColumns(id: string) {
    setError(null);
    setNote(null);
    setCheckResult(null);
    if (!id) {
      setColumns([]);
      return;
    }
    try {
      const d = await rulesApi.getColumns(id);
      setColumns(d.columns);
      setSuggestions(d.suggestions ?? []);
      setMasters(d.masters);
      setDraft(Object.fromEntries(d.columns.filter((c) => c.rule).map((c) => [c.key, { ...c.rule }])));
    } catch (err) {
      setError(errText(err, "Could not load the columns."));
    }
  }

  useEffect(() => { loadColumns(groupId); }, [groupId]);

  const setRule = (key: string, patch: ErpRule) => {
    setDraft((d) => {
      const next = { ...(d[key] ?? {}), ...patch };
      const empty = Object.values(next).every((v) => v === undefined || v === "" || v === false);
      const copy = { ...d };
      if (empty) delete copy[key];
      else copy[key] = next;
      return copy;
    });
  };

  async function save() {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const r = await rulesApi.saveRules(groupId, draft);
      setNote(`Saved ${r.saved} rule(s).`);
      await loadColumns(groupId);
      setTemplates(await rulesApi.listTemplates());
    } catch (err) {
      setError(errText(err, "Could not save."));
    } finally {
      setBusy(false);
    }
  }

  async function decide(s: RuleSuggestion, accept: boolean) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      if (accept) await rulesApi.acceptSuggestion(groupId, s.key);
      else await rulesApi.dismissSuggestion(groupId, s.key);
      setNote(accept ? `Rule added to ${s.header}.` : `Suggestion for ${s.header} dismissed.`);
      await loadColumns(groupId);
      setTemplates(await rulesApi.listTemplates());
    } catch (err) {
      setError(errText(err, "Could not update the suggestion."));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!copySource) return;
    setBusy(true);
    setError(null);
    try {
      const r = await rulesApi.copyFrom(groupId, copySource);
      setNote(`Copied ${r.copied} rule(s)${r.skipped ? `, ${r.skipped} skipped (no such column here)` : ""}.`);
      await loadColumns(groupId);
    } catch (err) {
      setError(errText(err, "Could not copy."));
    } finally {
      setBusy(false);
    }
  }

  async function check() {
    if (!jobRef.trim()) return;
    setBusy(true);
    setError(null);
    setCheckResult(null);
    try {
      setCheckResult(await rulesApi.checkJob(groupId, jobRef.trim()));
    } catch (err) {
      setError(errText(err, "Could not check that job."));
    } finally {
      setBusy(false);
    }
  }

  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return columns.filter((c) =>
      (!f || `${c.sheet} ${c.header} ${c.field}`.toLowerCase().includes(f)) &&
      (!onlyRules || c.builtin || draft[c.key]));
  }, [columns, filter, onlyRules, draft]);

  const field = "rounded border border-slate-300 px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-900";
  return (
    <AppShell title="ERP Column Rules" subtitle="What each ERP Excel column accepts - checked before anything is sent to the ERP.">
      <div className="space-y-4">
        {error && <Alert>{error}</Alert>}
        <Card className="p-4">
          <div className="flex flex-wrap items-center gap-3">
            <select value={groupId} onChange={(e) => setGroupId(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900">
              <option value="">Choose a template (Excel entry)…</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>{t.tenant} — {t.name}{t.rules_saved ? ` (${t.rules_saved} rules)` : ""}{t.suggestions ? ` · ${t.suggestions} suggested` : ""}</option>
              ))}
            </select>
            {groupId && (
              <>
                <select value={copySource} onChange={(e) => setCopySource(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900">
                  <option value="">Copy rules from…</option>
                  {templates.filter((t) => t.id !== groupId && t.rules_saved).map((t) => (
                    <option key={t.id} value={t.id}>{t.tenant} — {t.name} ({t.rules_saved})</option>
                  ))}
                </select>
                <button type="button" onClick={copy} disabled={!copySource || busy} className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium disabled:opacity-50 dark:border-slate-700">Copy</button>
              </>
            )}
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Country-code columns are checked without setup: a country written in full is turned into its code when certain
            ("POLAND" → PL); anything else stops the job before the ERP with a Fix card for the operator. Set a rule to change it, or "Off" to remove it.
          </p>
        </Card>

        {groupId && (
          <>
            {suggestions.length > 0 && (
              <Card className="border-amber-200 p-4 dark:border-amber-500/30">
                <h2 className="font-semibold text-slate-900 dark:text-slate-50">Suggested from ERP rejections ({suggestions.length})</h2>
                <p className="mb-3 text-xs text-slate-500">The ERP refused these columns on real jobs. Add the rule and the same value is stopped in Cargora next time, before the ERP.</p>
                <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                  {suggestions.map((s) => (
                    <li key={s.key} className="flex flex-wrap items-center justify-between gap-3 py-2 text-sm">
                      <div>
                        <p><span className="text-xs text-slate-500">{s.sheet}</span> <b>{s.header}</b>{s.field ? <span className="text-xs text-slate-500"> ← {s.field}</span> : null}</p>
                        <p className="text-xs text-slate-600 dark:text-slate-300">
                          ERP said: “{s.erp_text}” · {s.count}× · last {s.last_job}
                        </p>
                        <p className="text-xs font-medium text-indigo-700 dark:text-indigo-300">Rule: {s.rule_text}</p>
                      </div>
                      <div className="flex gap-2">
                        {!s.rule.needs_list && (
                          <button type="button" disabled={busy} onClick={() => decide(s, true)} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-60">Add rule</button>
                        )}
                        {s.rule.needs_list && (
                          <button type="button" onClick={() => setFilter(s.header)} className="rounded-lg border border-indigo-300 px-3 py-1.5 text-xs font-semibold text-indigo-700 dark:text-indigo-300">Set list on the column</button>
                        )}
                        <button type="button" disabled={busy} onClick={() => decide(s, false)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium disabled:opacity-60 dark:border-slate-700">Dismiss</button>
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
            <Card className="p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Find a column" className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900" />
                  <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={onlyRules} onChange={(e) => setOnlyRules(e.target.checked)} />Only columns with a rule</label>
                </div>
                <div className="flex items-center gap-3">
                  {note && <span className="text-sm text-emerald-700 dark:text-emerald-300">{note}</span>}
                  <button type="button" onClick={save} disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-60">Save rules</button>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-slate-500">
                    <tr>
                      <th className="py-2 pr-2">Sheet</th><th className="pr-2">ERP column</th><th className="pr-2">Cargora field</th>
                      <th className="pr-2">Required</th><th className="pr-2">Length</th><th className="pr-2">From list</th>
                      <th className="pr-2">Number</th><th className="pr-2">Pattern / message</th><th className="pr-2">Off</th><th>Checked as</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((c) => {
                      const r = draft[c.key] ?? {};
                      return (
                        <tr key={c.key} className="border-t border-slate-100 align-top dark:border-slate-800">
                          <td className="py-1.5 pr-2 text-xs text-slate-500">{c.sheet}</td>
                          <td className="pr-2 font-medium">{c.header}</td>
                          <td className="pr-2 text-xs">{c.field || (c.fixed_value != null ? <span className="text-slate-400">fixed: {c.fixed_value}</span> : <span className="text-slate-400">—</span>)}</td>
                          <td className="pr-2"><input type="checkbox" checked={!!r.required} onChange={(e) => setRule(c.key, { required: e.target.checked })} /></td>
                          <td className="pr-2">
                            <div className="flex gap-1">
                              <select value={r.length_mode ?? "max"} onChange={(e) => setRule(c.key, { length_mode: e.target.value as "exact" | "max" })} className={field}>
                                <option value="max">max</option><option value="exact">exact</option>
                              </select>
                              <input type="number" min={1} value={r.length ?? ""} onChange={(e) => setRule(c.key, { length: e.target.value ? Number(e.target.value) : undefined })} className={`${field} w-16`} />
                            </div>
                          </td>
                          <td className="pr-2">
                            <select value={r.master ?? ""} onChange={(e) => setRule(c.key, { master: (e.target.value || undefined) as ErpRule["master"] })} className={field} title={r.master ? masters[r.master] : ""}>
                              <option value="">—</option><option value="country">Country code</option><option value="currency">Currency code</option><option value="unit">Unit (UQC)</option>
                            </select>
                          </td>
                          <td className="pr-2"><input type="checkbox" checked={!!r.number} onChange={(e) => setRule(c.key, { number: e.target.checked })} /></td>
                          <td className="pr-2">
                            <input value={r.pattern ?? ""} onChange={(e) => setRule(c.key, { pattern: e.target.value || undefined })} placeholder="regex" className={`${field} w-28`} />
                            {r.pattern && <input value={r.message ?? ""} onChange={(e) => setRule(c.key, { message: e.target.value || undefined })} placeholder="message for the operator" className={`${field} mt-1 w-40`} />}
                          </td>
                          <td className="pr-2"><input type="checkbox" checked={!!r.off} onChange={(e) => setRule(c.key, { off: e.target.checked })} title="No check on this column" /></td>
                          <td className="text-xs text-slate-600 dark:text-slate-300">
                            {r.off ? <span className="text-slate-400">Not checked</span>
                              : draft[c.key] ? <span className="text-indigo-700 dark:text-indigo-300">Your rule (save to apply)</span>
                                : c.builtin_text ? <span title={c.builtin_text}>Built in: {c.builtin?.master === "currency" ? "currency code" : "country code"}</span>
                                  : <span className="text-slate-400">—</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card className="p-4">
              <h2 className="mb-2 font-semibold text-slate-900 dark:text-slate-50">Try the saved rules on a job</h2>
              <div className="flex gap-2">
                <input value={jobRef} onChange={(e) => setJobRef(e.target.value)} placeholder="JOB-…" className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900" />
                <button type="button" onClick={check} disabled={busy} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium dark:border-slate-700">Check</button>
              </div>
              {checkResult && (
                checkResult.items.length === 0 ? (
                  <p className="mt-2 text-sm text-emerald-700 dark:text-emerald-300">✓ {checkResult.reference} passes every rule - it would go to the ERP.</p>
                ) : (
                  <ul className="mt-2 space-y-1 text-sm">
                    {checkResult.items.map((i, n) => (
                      <li key={n} className="text-rose-700 dark:text-rose-300">
                        <b>{i.display}</b>{i.where ? ` (${i.where})` : ""}: sent {i.sent.map((s) => `"${s}"`).join(", ") || "(empty)"} — {i.needs}
                      </li>
                    ))}
                  </ul>
                )
              )}
            </Card>
          </>
        )}
      </div>
    </AppShell>
  );
}
