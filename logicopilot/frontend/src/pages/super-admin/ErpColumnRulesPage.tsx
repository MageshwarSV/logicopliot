/** Super Admin - ERP Field Configuration (R7 + R2, 2026-11-28; was "ERP Column Rules", P2).
 *  Pick a template and see every ERP Excel column: which document / field feeds it, whether it
 *  is mandatory, the rules that check it, where it lands (sheet / column / ERP field) and whether
 *  the mapping works (Configured / Missing / Invalid / Not used).
 *
 *  Mandatory has ONE meaning: the column's rule "required" - the pre-ERP check stops a job
 *  whose mandatory column is empty (backend app/core/erp_rules.py, app/core/erp_field_map.py).
 *  Suggestions come from: ask-operator-required fields, ERP refusals, onboarding's ERP field
 *  list, and (Analyse recent jobs) columns filled on almost every job the ERP accepted. */
import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Alert } from "../../components/ui/Alert";
import * as rulesApi from "../../api/erpRules";
import type { ConfigRow, ErpRule, FieldConfig, MappingStatus, RuleCheckItem, RuleSuggestion, RuleTemplate } from "../../api/erpRules";
import { formatDateTime } from "../../utils/dateTime";
import {
  CheckBadgeIcon, CheckCircleIcon, ChevronDownIcon, DatabaseIcon, DocumentIcon, ExclamationTriangleIcon,
  FilterIcon, LayersIcon, PencilIcon, SearchIcon, SparklesIcon,
} from "../../components/ui/icons";

function errText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) && typeof err.response?.data?.detail === "string" ? err.response.data.detail : fallback;
}

const STATUS: Record<MappingStatus, { label: string; cls: string; dot: string }> = {
  configured: { label: "Configured", cls: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/10 dark:text-emerald-300", dot: "bg-emerald-500" },
  missing: { label: "Missing", cls: "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-500/10 dark:text-rose-300", dot: "bg-rose-500" },
  invalid: { label: "Invalid", cls: "bg-amber-50 text-amber-800 ring-amber-600/30 dark:bg-amber-500/10 dark:text-amber-300", dot: "bg-amber-500" },
  not_used: { label: "Not used", cls: "bg-slate-50 text-slate-500 ring-slate-400/20 dark:bg-slate-800 dark:text-slate-400", dot: "bg-slate-300" },
};

/** A stable colour per document name, so the same document always looks the same. */
const DOC_TONES = [
  "bg-sky-50 text-sky-700 ring-sky-600/20 dark:bg-sky-500/10 dark:text-sky-300",
  "bg-violet-50 text-violet-700 ring-violet-600/20 dark:bg-violet-500/10 dark:text-violet-300",
  "bg-teal-50 text-teal-700 ring-teal-600/20 dark:bg-teal-500/10 dark:text-teal-300",
  "bg-orange-50 text-orange-700 ring-orange-600/20 dark:bg-orange-500/10 dark:text-orange-300",
  "bg-pink-50 text-pink-700 ring-pink-600/20 dark:bg-pink-500/10 dark:text-pink-300",
  "bg-lime-50 text-lime-800 ring-lime-600/20 dark:bg-lime-500/10 dark:text-lime-300",
];
function docTone(name: string): string {
  if (/fixed value/i.test(name)) return "bg-slate-100 text-slate-600 ring-slate-400/20 dark:bg-slate-800 dark:text-slate-300";
  if (/job field|lookup|combined/i.test(name)) return "bg-indigo-50 text-indigo-700 ring-indigo-600/20 dark:bg-indigo-500/10 dark:text-indigo-300";
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return DOC_TONES[h % DOC_TONES.length];
}
const docsOf = (r: ConfigRow) => (r.document ? r.document.split(",").map((d) => d.trim()).filter(Boolean) : []);

type MandFilter = "all" | "yes" | "no" | "suggested";
type StatusFilter = "all" | MappingStatus;
type CardKey = "configured" | "missing" | "invalid" | "mandatory" | "suggested" | "refused";

export function ErpColumnRulesPage() {
  const [templates, setTemplates] = useState<RuleTemplate[]>([]);
  const [groupId, setGroupId] = useState("");
  const [cfg, setCfg] = useState<FieldConfig | null>(null);
  const [suggestions, setSuggestions] = useState<RuleSuggestion[]>([]);
  const [draft, setDraft] = useState<Record<string, ErpRule>>({});
  const [saved, setSaved] = useState<Record<string, ErpRule>>({});
  // filters
  const [q, setQ] = useState("");
  const [doc, setDoc] = useState("");
  const [sheet, setSheet] = useState("");
  const [mand, setMand] = useState<MandFilter>("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [onlyRefused, setOnlyRefused] = useState(false);
  const [showUnused, setShowUnused] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [help, setHelp] = useState(false);
  // tools
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
      const initial = Object.fromEntries(c.rows.filter((r) => r.rule).map((r) => [r.key, { ...r.rule }]));
      setCfg(c);
      setSuggestions(cols.suggestions ?? []);
      setDraft(initial);
      setSaved(initial);
    } catch (err) {
      setError(errText(err, "Could not load the configuration."));
    }
  }

  useEffect(() => { setNote(null); clearFilters(); load(groupId); }, [groupId]); // eslint-disable-line react-hooks/exhaustive-deps

  const changedKeys = useMemo(() => {
    const keys = new Set([...Object.keys(draft), ...Object.keys(saved)]);
    return [...keys].filter((k) => JSON.stringify(draft[k] ?? null) !== JSON.stringify(saved[k] ?? null));
  }, [draft, saved]);
  const dirty = changedKeys.length > 0;

  const setRule = (key: string, patch: ErpRule) => {
    setDraft((d) => {
      const next = { ...(d[key] ?? {}), ...patch };
      const empty = Object.values(next).every((v) => v === undefined || v === "" || v === false || (Array.isArray(v) && !v.length));
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
    return `Saved - ${r.saved} column rule(s) in force.`;
  });
  const discard = () => { setDraft(saved); setEditing(null); };

  const analyse = () => run("analyse", async () => {
    const r = await rulesApi.analyseJobs(groupId);
    await load(groupId);
    return `Analysed ${r.jobs} job(s) the ERP accepted.`;
  });

  const acceptStrong = () => run("strong", async () => {
    if (!cfg) return;
    if (dirty && !window.confirm("You have unsaved changes - they will be lost. Continue?")) return;
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

  function clearFilters() {
    setQ(""); setDoc(""); setSheet(""); setMand("all"); setStatus("all");
    setOnlyProblems(false); setOnlyRefused(false); setShowUnused(false);
  }
  const filtersOn = !!(q || doc || sheet || mand !== "all" || status !== "all" || onlyProblems || onlyRefused || showUnused);

  const rows = useMemo(() => cfg?.rows ?? [], [cfg]);
  const isMandatory = (r: ConfigRow) => (draft[r.key] ? !!draft[r.key].required && !draft[r.key].off : r.mandatory);
  const documents = useMemo(() => Array.from(new Set(rows.flatMap(docsOf))).sort(), [rows]);
  const sheets = useMemo(() => Array.from(new Set(rows.map((r) => r.sheet))), [rows]);

  const shown = useMemo(() => {
    const f = q.trim().toLowerCase();
    return rows.filter((r) => {
      if (sheet && r.sheet !== sheet) return false;
      if (doc && !docsOf(r).includes(doc)) return false;
      if (f && !`${r.sheet} ${r.header} ${r.doc_field} ${r.document} ${r.erp_field} ${r.column}`.toLowerCase().includes(f)) return false;
      const m = draft[r.key] ? !!draft[r.key].required && !draft[r.key].off : r.mandatory;
      if (mand === "yes" && !m) return false;
      if (mand === "no" && m) return false;
      if (mand === "suggested" && !(r.suggest_mandatory && !m)) return false;
      if (status !== "all" && r.status !== status) return false;
      if (onlyProblems && !r.problems.length && r.status !== "missing") return false;
      if (onlyRefused && !r.refused) return false;
      const narrowed = status !== "all" || mand !== "all" || onlyProblems || onlyRefused || !!doc;
      if (!narrowed && !showUnused && r.status === "not_used" && !m) return false;
      return true;
    });
  }, [rows, q, doc, sheet, mand, status, onlyProblems, onlyRefused, showUnused, draft]);

  const bySheet = useMemo(() => {
    const out: { sheet: string; rows: ConfigRow[] }[] = [];
    for (const r of shown) {
      const last = out[out.length - 1];
      if (last && last.sheet === r.sheet) last.rows.push(r);
      else out.push({ sheet: r.sheet, rows: [r] });
    }
    return out;
  }, [shown]);

  const s = cfg?.summary;
  const activeCard: CardKey | null =
    status === "configured" ? "configured" : status === "missing" ? "missing" : status === "invalid" ? "invalid"
      : mand === "yes" ? "mandatory" : mand === "suggested" ? "suggested" : onlyRefused ? "refused" : null;
  const pickCard = (c: CardKey) => {
    const same = activeCard === c;
    setStatus("all"); setMand("all"); setOnlyRefused(false); setOnlyProblems(false);
    if (same) return;
    if (c === "configured" || c === "missing" || c === "invalid") setStatus(c);
    if (c === "mandatory") setMand("yes");
    if (c === "suggested") setMand("suggested");
    if (c === "refused") setOnlyRefused(true);
  };

  const sel = "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-900 dark:focus:ring-indigo-500/20";
  const input = "rounded-md border border-slate-300 px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-900";
  let sno = 0;

  return (
    <AppShell title="ERP Field Configuration" subtitle="Every ERP Excel column - which document fills it, whether it is mandatory, how it is checked and whether the mapping works.">
      <div className="space-y-5 pb-24">
        {error && <Alert>{error}</Alert>}

        {/* ---------- template + analyse */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex flex-wrap items-end gap-4">
            <label className="flex min-w-[18rem] flex-1 flex-col gap-1">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">ERP template</span>
              <select value={groupId} onChange={(e) => setGroupId(e.target.value)} className={`${sel} py-2.5 text-base`}>
                <option value="">Choose a template…</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>{t.tenant} — {t.name}{t.rules_saved ? ` · ${t.rules_saved} rules` : ""}</option>
                ))}
              </select>
            </label>
            {cfg && (
              <>
                <div className="flex flex-col gap-1">
                  <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">ERP Excel file</span>
                  <span className="inline-flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2.5 text-sm font-medium text-slate-800 ring-1 ring-inset ring-slate-200 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700">
                    <DocumentIcon className="h-4 w-4 text-emerald-600" />{cfg.excel_file || "—"}
                  </span>
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {cfg.analysis.at ? `Analysed ${formatDateTime(cfg.analysis.at)} · ${cfg.analysis.jobs} jobs` : "Not analysed yet"}
                  </span>
                  <button type="button" onClick={analyse} disabled={!!busy}
                    className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-2.5 text-sm font-semibold text-indigo-700 hover:bg-indigo-100 disabled:opacity-60 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-300"
                    title="Measure how often each column had a value on recent jobs the ERP accepted - feeds the Filled % and suggestions">
                    <SparklesIcon className="h-4 w-4" />{busy === "analyse" ? "Analysing…" : cfg.analysis.at ? "Analyse again" : "Analyse recent jobs"}
                  </button>
                </div>
              </>
            )}
            <button type="button" onClick={() => setHelp((h) => !h)} className="ml-auto text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400">
              {help ? "Hide help" : "How does this work?"}
            </button>
          </div>
          {help && (
            <div className="mt-4 grid gap-3 rounded-xl bg-slate-50 p-4 text-sm text-slate-600 sm:grid-cols-3 dark:bg-slate-800/60 dark:text-slate-300">
              <p><b className="text-slate-800 dark:text-slate-100">Mandatory</b> - a job with this column empty is stopped before the ERP and the operator gets a Fix card. Auto ERP jobs still get a value filled in.</p>
              <p><b className="text-slate-800 dark:text-slate-100">Rules</b> - length, a list of allowed values, country / currency / unit code, number or a pattern. Checked on every job before it goes to the ERP.</p>
              <p><b className="text-slate-800 dark:text-slate-100">Status</b> - Configured works; Missing = mandatory with no source; Invalid = its source does not exist, so it always goes out empty.</p>
            </div>
          )}
        </section>

        {!cfg && !error && (
          <div className="rounded-2xl border border-dashed border-slate-300 p-12 text-center text-slate-500 dark:border-slate-700">
            <DatabaseIcon className="mx-auto mb-3 h-8 w-8 text-slate-400" />
            Choose an ERP template above to see its columns.
          </div>
        )}

        {cfg && s && (
          <>
            {/* ---------- summary cards (click to filter) */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7">
              <SumCard label="Columns in use" n={s.total - s.not_used} sub={`${s.not_used} not used`} icon={<LayersIcon className="h-4 w-4" />} tone="slate" active={activeCard === null && !filtersOn} onClick={clearFilters} />
              <SumCard label="Configured" n={s.configured} icon={<CheckCircleIcon className="h-4 w-4" />} tone="green" active={activeCard === "configured"} onClick={() => pickCard("configured")} />
              <SumCard label="Missing" n={s.missing} sub="mandatory, no source" icon={<ExclamationTriangleIcon className="h-4 w-4" />} tone={s.missing ? "red" : "slate"} active={activeCard === "missing"} onClick={() => pickCard("missing")} />
              <SumCard label="Invalid" n={s.invalid} sub="always goes out empty" icon={<ExclamationTriangleIcon className="h-4 w-4" />} tone={s.invalid ? "amber" : "slate"} active={activeCard === "invalid"} onClick={() => pickCard("invalid")} />
              <SumCard label="Mandatory" n={s.mandatory} icon={<CheckBadgeIcon className="h-4 w-4" />} tone="indigo" active={activeCard === "mandatory"} onClick={() => pickCard("mandatory")} />
              <SumCard label="Suggested" n={s.suggested} sub={s.suggested_strong ? `${s.suggested_strong} strong` : undefined} icon={<SparklesIcon className="h-4 w-4" />} tone="violet" active={activeCard === "suggested"} onClick={() => pickCard("suggested")} />
              <SumCard label="Refused by ERP" n={s.refused} sub="last 90 days" icon={<ExclamationTriangleIcon className="h-4 w-4" />} tone={s.refused ? "red" : "slate"} active={activeCard === "refused"} onClick={() => pickCard("refused")} />
            </div>

            {/* ---------- one-click suggestions */}
            {s.suggested_strong > 0 && (
              <section className="flex flex-wrap items-center gap-4 rounded-2xl border border-indigo-200 bg-gradient-to-r from-indigo-50 to-white p-4 dark:border-indigo-500/30 dark:from-indigo-500/10 dark:to-slate-900">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-white"><SparklesIcon className="h-5 w-5" /></div>
                <div className="min-w-0 flex-1 text-sm text-slate-700 dark:text-slate-200">
                  <b>{s.suggested_strong} column(s) look mandatory</b> - the ERP refused them as empty, the job screen already requires
                  them, or onboarding marks them. Mark them and a job with one empty is stopped before the ERP.
                </div>
                <button type="button" onClick={() => pickCard("suggested")} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900">Review them</button>
                <button type="button" onClick={acceptStrong} disabled={!!busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-60">Mark them mandatory</button>
              </section>
            )}

            {suggestions.length > 0 && (
              <section className="rounded-2xl border border-amber-200 bg-amber-50/40 p-4 dark:border-amber-500/30 dark:bg-amber-500/5">
                <h2 className="font-semibold text-slate-900 dark:text-slate-50">Rules suggested from ERP rejections <span className="text-slate-500">({suggestions.length})</span></h2>
                <p className="mb-3 text-xs text-slate-500">The ERP refused these columns on real jobs. Add the rule and the same value is stopped in Cargora next time.</p>
                <ul className="space-y-2">
                  {suggestions.map((sg) => (
                    <li key={sg.key} className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-white p-3 text-sm shadow-sm ring-1 ring-amber-100 dark:bg-slate-900 dark:ring-amber-500/20">
                      <div className="min-w-0">
                        <p><span className="text-xs text-slate-500">{sg.sheet}</span> <b>{sg.header}</b>{sg.field ? <span className="text-xs text-slate-500"> ← {sg.field}</span> : null}</p>
                        <p className="text-xs text-slate-600 dark:text-slate-300">ERP said: “{sg.erp_text}” · {sg.count}× · last {sg.last_job}</p>
                        <p className="text-xs font-semibold text-indigo-700 dark:text-indigo-300">Rule: {sg.rule_text}</p>
                      </div>
                      <div className="flex gap-2">
                        {!sg.rule.needs_list
                          ? <button type="button" disabled={!!busy} onClick={() => decide(sg, true)} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-60">Add rule</button>
                          : <button type="button" onClick={() => { setQ(sg.header); setEditing(sg.key); }} className="rounded-lg border border-indigo-300 px-3 py-1.5 text-xs font-semibold text-indigo-700 dark:text-indigo-300">Set list on the column</button>}
                        <button type="button" disabled={!!busy} onClick={() => decide(sg, false)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium disabled:opacity-60 dark:border-slate-700">Dismiss</button>
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* ---------- filters + table */}
            <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
              <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 p-4 dark:border-slate-800">
                <div className="relative min-w-[14rem] flex-1">
                  <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                  <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search column, field or document…" className={`${sel} w-full pl-9`} />
                </div>
                <select value={doc} onChange={(e) => setDoc(e.target.value)} className={sel} aria-label="Document">
                  <option value="">All documents</option>
                  {documents.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
                <select value={sheet} onChange={(e) => setSheet(e.target.value)} className={sel} aria-label="Sheet">
                  <option value="">All sheets</option>
                  {sheets.map((x) => <option key={x} value={x}>{x}</option>)}
                </select>
                <select value={mand} onChange={(e) => setMand(e.target.value as MandFilter)} className={sel} aria-label="Mandatory">
                  <option value="all">Mandatory: any</option>
                  <option value="yes">Mandatory only</option>
                  <option value="no">Not mandatory</option>
                  <option value="suggested">Suggested mandatory</option>
                </select>
                <select value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)} className={sel} aria-label="Status">
                  <option value="all">Status: any</option>
                  <option value="configured">Configured</option>
                  <option value="missing">Missing</option>
                  <option value="invalid">Invalid</option>
                  <option value="not_used">Not used</option>
                </select>
                <Toggle on={onlyProblems} set={setOnlyProblems} label="Only problems" />
                <Toggle on={showUnused} set={setShowUnused} label="Show unused" />
                {filtersOn && (
                  <button type="button" onClick={clearFilters} className="inline-flex items-center gap-1 rounded-lg px-2 py-2 text-sm font-medium text-slate-500 hover:text-slate-800 dark:hover:text-slate-200">
                    <FilterIcon className="h-4 w-4" />Clear filters
                  </button>
                )}
                <span className="ml-auto text-xs text-slate-500">{shown.length} of {rows.length} columns</span>
              </div>

              {note && <div className="border-b border-emerald-100 bg-emerald-50 px-4 py-2 text-sm text-emerald-800 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">✓ {note}</div>}

              {shown.length === 0 ? (
                <div className="p-12 text-center text-sm text-slate-500">No columns match these filters. <button type="button" onClick={clearFilters} className="font-semibold text-indigo-600 hover:underline">Clear filters</button></div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 z-10 bg-slate-50 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:bg-slate-800/80 dark:text-slate-400">
                      <tr>
                        <th className="w-12 px-3 py-3 text-center">S.No</th>
                        <th className="px-3 py-3">ERP column</th>
                        <th className="px-3 py-3">Document → field</th>
                        <th className="px-3 py-3">Mandatory</th>
                        <th className="px-3 py-3">Validation rules</th>
                        <th className="px-3 py-3">Status</th>
                        <th className="px-3 py-3 text-right" title="ERP refusals, last 90 days">Refused</th>
                        <th className="px-3 py-3 text-right" title="Had a value on this % of recent jobs the ERP accepted">Filled</th>
                      </tr>
                    </thead>
                    <tbody>
                      {bySheet.map((grp) => {
                        const isCollapsed = !!collapsed[grp.sheet];
                        const mandN = grp.rows.filter(isMandatory).length;
                        const probN = grp.rows.filter((r) => r.status === "invalid" || r.status === "missing").length;
                        return (
                          <Fragment key={grp.sheet}>
                            <tr className="border-y border-slate-200 bg-slate-100/70 dark:border-slate-700 dark:bg-slate-800/60">
                              <td colSpan={8} className="px-3 py-2">
                                <button type="button" onClick={() => setCollapsed((c) => ({ ...c, [grp.sheet]: !c[grp.sheet] }))} className="flex w-full items-center gap-3 text-left">
                                  <ChevronDownIcon className={`h-4 w-4 text-slate-500 transition-transform ${isCollapsed ? "-rotate-90" : ""}`} />
                                  <span className="font-bold tracking-wide text-slate-800 dark:text-slate-100">{grp.sheet}</span>
                                  <span className="text-xs text-slate-500">{grp.rows.length} column(s)</span>
                                  {mandN > 0 && <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-[11px] font-semibold text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300">{mandN} mandatory</span>}
                                  {probN > 0 && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800 dark:bg-amber-500/20 dark:text-amber-300">{probN} problem(s)</span>}
                                  {grp.rows[0]?.per_line && <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[11px] font-medium text-slate-600 dark:bg-slate-700 dark:text-slate-300">one row per product line</span>}
                                </button>
                              </td>
                            </tr>
                            {!isCollapsed && grp.rows.map((r) => {
                              sno += 1;
                              const d = draft[r.key] ?? {};
                              const mandOn = isMandatory(r);
                              const changed = changedKeys.includes(r.key);
                              return (
                                <Fragment key={r.key}>
                                  <tr className={`border-b border-slate-100 align-top transition-colors hover:bg-indigo-50/30 dark:border-slate-800 dark:hover:bg-slate-800/40 ${changed ? "bg-indigo-50/40 dark:bg-indigo-500/5" : ""}`}>
                                    <td className="px-3 py-3 text-center text-xs tabular-nums text-slate-400">{sno}</td>
                                    <td className="px-3 py-3">
                                      <div className="flex items-center gap-2">
                                        {r.column && <span className="inline-flex h-6 min-w-[1.5rem] items-center justify-center rounded-md bg-slate-100 px-1 font-mono text-[11px] font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">{r.column}</span>}
                                        <span className="font-semibold text-slate-900 dark:text-slate-50">{r.header}</span>
                                      </div>
                                      {r.erp_field && r.erp_field !== r.header && <p className="mt-0.5 pl-8 text-xs text-slate-500">ERP field: {r.erp_field}</p>}
                                    </td>
                                    <td className="px-3 py-3">
                                      <div className="flex flex-wrap gap-1">
                                        {docsOf(r).length
                                          ? docsOf(r).map((x) => <span key={x} className={`rounded-md px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${docTone(x)}`}>{x}</span>)
                                          : <span className="text-xs text-slate-400">No source</span>}
                                      </div>
                                      {r.doc_field && <p className="mt-1 text-xs font-medium text-slate-700 dark:text-slate-200">{r.doc_field}</p>}
                                    </td>
                                    <td className="px-3 py-3">
                                      <Switch on={mandOn} onChange={(v) => setRule(r.key, { required: v, off: false })} />
                                      {r.suggest_mandatory && !mandOn && (
                                        <span title={r.mandatory_reasons.join("\n")}
                                          className={`mt-1.5 block w-fit cursor-help rounded px-1.5 py-0.5 text-[10px] font-semibold ${r.suggest_strength === "strong" ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"}`}>
                                          Suggested{r.suggest_strength === "data" ? " · data" : ""}
                                        </span>
                                      )}
                                    </td>
                                    <td className="max-w-[18rem] px-3 py-3">
                                      <div className="flex flex-wrap items-center gap-1">
                                        {r.rules.length
                                          ? r.rules.map((t, n) => <span key={n} className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-700 dark:bg-slate-800 dark:text-slate-200">{t}</span>)
                                          : <span className="text-xs text-slate-400">No rules</span>}
                                        <button type="button" onClick={() => setEditing(editing === r.key ? null : r.key)} title="Edit rules"
                                          className={`inline-flex h-6 w-6 items-center justify-center rounded-md transition ${editing === r.key ? "bg-indigo-600 text-white" : "text-slate-400 hover:bg-slate-100 hover:text-indigo-600 dark:hover:bg-slate-800"}`}>
                                          <PencilIcon className="h-3.5 w-3.5" />
                                        </button>
                                      </div>
                                      {changed && <p className="mt-1 text-[11px] font-semibold text-indigo-700 dark:text-indigo-300">Changed - not saved yet</p>}
                                    </td>
                                    <td className="px-3 py-3">
                                      <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${STATUS[r.status].cls}`}>
                                        <span className={`h-1.5 w-1.5 rounded-full ${STATUS[r.status].dot}`} />{STATUS[r.status].label}
                                      </span>
                                      {r.problems.map((p, n) => <p key={n} className="mt-1 max-w-[16rem] text-[11px] text-amber-700 dark:text-amber-300">⚠ {p}</p>)}
                                    </td>
                                    <td className="px-3 py-3 text-right text-xs tabular-nums">
                                      {r.refused ? <span className="font-semibold text-rose-700 dark:text-rose-300" title={r.refused_last ? `Last: ${r.refused_last}` : ""}>{r.refused}×</span> : <span className="text-slate-300">—</span>}
                                    </td>
                                    <td className="px-3 py-3 text-right text-xs tabular-nums">
                                      {r.fill_pct == null ? <span className="text-slate-300">—</span> : <FillBar pct={r.fill_pct} warn={r.fill_pct < 50 && r.status === "configured"} />}
                                    </td>
                                  </tr>
                                  {editing === r.key && (
                                    <tr className="border-b border-indigo-100 bg-indigo-50/40 dark:border-indigo-500/20 dark:bg-indigo-500/5">
                                      <td />
                                      <td colSpan={7} className="px-3 py-4">
                                        <div className="flex flex-wrap items-end gap-4 text-xs">
                                          <label className="flex flex-col gap-1 font-medium">Length
                                            <span className="flex gap-1">
                                              <select value={d.length_mode ?? "max"} onChange={(e) => setRule(r.key, { length_mode: e.target.value as "exact" | "max" })} className={input}>
                                                <option value="max">at most</option><option value="exact">exactly</option>
                                              </select>
                                              <input type="number" min={1} value={d.length ?? ""} onChange={(e) => setRule(r.key, { length: e.target.value ? Number(e.target.value) : undefined })} className={`${input} w-16`} />
                                            </span>
                                          </label>
                                          <label className="flex flex-col gap-1 font-medium">From list
                                            <select value={d.master ?? ""} onChange={(e) => setRule(r.key, { master: (e.target.value || undefined) as ErpRule["master"] })} className={input}>
                                              <option value="">—</option><option value="country">Country code</option><option value="currency">Currency code</option><option value="unit">Unit (UQC)</option>
                                            </select>
                                          </label>
                                          <label className="flex flex-col gap-1 font-medium">Allowed values (comma separated)
                                            <input key={(d.allowed ?? []).join(",")} defaultValue={(d.allowed ?? []).join(", ")}
                                              onBlur={(e) => {
                                                const list = e.target.value.split(",").map((x) => x.trim().toUpperCase()).filter(Boolean);
                                                if (list.join(",") !== (d.allowed ?? []).join(",")) setRule(r.key, { allowed: list.length ? list : undefined });
                                              }}
                                              className={`${input} w-56`} placeholder="e.g. PKG, PLT, CTN" />
                                            {r.accepted_values && r.accepted_values.length > 0 && (
                                              <button type="button" onClick={() => setRule(r.key, { allowed: r.accepted_values ?? undefined })}
                                                className="self-start text-[11px] font-semibold text-indigo-600 hover:underline dark:text-indigo-400" title={r.accepted_values.join(", ")}>
                                                Use the {r.accepted_values.length} value(s) the ERP accepted
                                              </button>
                                            )}
                                          </label>
                                          <label className="flex items-center gap-1.5"><input type="checkbox" checked={!!d.number} onChange={(e) => setRule(r.key, { number: e.target.checked })} />Number</label>
                                          <label className="flex items-center gap-1.5"><input type="checkbox" checked={!!d.uppercase} onChange={(e) => setRule(r.key, { uppercase: e.target.checked })} />Uppercase</label>
                                          <label className="flex flex-col gap-1 font-medium">Pattern (regex)
                                            <input value={d.pattern ?? ""} onChange={(e) => setRule(r.key, { pattern: e.target.value || undefined })} className={`${input} w-40`} />
                                          </label>
                                          {d.pattern && (
                                            <label className="flex flex-col gap-1 font-medium">Message for the operator
                                              <input value={d.message ?? ""} onChange={(e) => setRule(r.key, { message: e.target.value || undefined })} className={`${input} w-56`} />
                                            </label>
                                          )}
                                          <label className="flex items-center gap-1.5" title="No check at all on this column (also turns off a built-in check)">
                                            <input type="checkbox" checked={!!d.off} onChange={(e) => setRule(r.key, { off: e.target.checked })} />Turn all checks off
                                          </label>
                                          <button type="button" onClick={() => setEditing(null)} className="ml-auto rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold dark:border-slate-700 dark:bg-slate-900">Done</button>
                                        </div>
                                        {r.mandatory_reasons.length > 0 && (
                                          <p className="mt-3 text-xs text-slate-500">Why it may be mandatory: {r.mandatory_reasons.join(" · ")}</p>
                                        )}
                                      </td>
                                    </tr>
                                  )}
                                </Fragment>
                              );
                            })}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* ---------- tools */}
            <div className="grid gap-4 md:grid-cols-2">
              <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <h2 className="font-semibold text-slate-900 dark:text-slate-50">Test the saved rules on a job</h2>
                <p className="mb-3 text-xs text-slate-500">Shows what a real job would be stopped for, right now.</p>
                <div className="flex gap-2">
                  <input value={jobRef} onChange={(e) => setJobRef(e.target.value)} onKeyDown={(e) => e.key === "Enter" && check()} placeholder="JOB-… or ERP job number" className={`${sel} flex-1`} />
                  <button type="button" onClick={check} disabled={!!busy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700 disabled:opacity-60 dark:bg-slate-100 dark:text-slate-900">Check</button>
                </div>
                {checkResult && (checkResult.items.length === 0 ? (
                  <p className="mt-3 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">✓ {checkResult.reference} passes every rule - it would go to the ERP.</p>
                ) : (
                  <ul className="mt-3 space-y-1.5 rounded-lg bg-rose-50 p-3 text-sm dark:bg-rose-500/10">
                    {checkResult.items.map((it, n) => (
                      <li key={n} className="text-rose-800 dark:text-rose-300">
                        <b>{it.display}</b>{it.where ? ` (${it.where})` : ""}: sent {it.sent.map((x) => `"${x}"`).join(", ") || "(empty)"} — {it.needs}
                      </li>
                    ))}
                  </ul>
                ))}
              </section>
              <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <h2 className="font-semibold text-slate-900 dark:text-slate-50">Copy rules from another template</h2>
                <p className="mb-3 text-xs text-slate-500">Same ERP, same columns - rules are copied for the columns this template also has.</p>
                <div className="flex gap-2">
                  <select value={copySource} onChange={(e) => setCopySource(e.target.value)} className={`${sel} flex-1`}>
                    <option value="">Choose a template…</option>
                    {templates.filter((t) => t.id !== groupId && t.rules_saved).map((t) => (
                      <option key={t.id} value={t.id}>{t.tenant} — {t.name} ({t.rules_saved})</option>
                    ))}
                  </select>
                  <button type="button" onClick={copy} disabled={!copySource || !!busy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900">Copy</button>
                </div>
              </section>
            </div>
          </>
        )}
      </div>

      {/* ---------- unsaved changes bar */}
      {dirty && (
        <div className="fixed inset-x-0 bottom-4 z-30 flex justify-center px-4">
          <div className="flex items-center gap-4 rounded-2xl bg-slate-900 px-5 py-3 text-sm text-white shadow-2xl ring-1 ring-black/10 dark:bg-slate-100 dark:text-slate-900">
            <span><b>{changedKeys.length}</b> unsaved change(s)</span>
            <button type="button" onClick={discard} className="rounded-lg px-3 py-1.5 font-medium text-slate-300 hover:text-white dark:text-slate-600 dark:hover:text-slate-900">Discard</button>
            <button type="button" onClick={save} disabled={!!busy} className="rounded-lg bg-indigo-500 px-4 py-1.5 font-semibold text-white hover:bg-indigo-400 disabled:opacity-60">{busy === "save" ? "Saving…" : "Save changes"}</button>
          </div>
        </div>
      )}
    </AppShell>
  );
}

const SUM_TONE = {
  slate: { icon: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300", ring: "ring-slate-400" },
  green: { icon: "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-300", ring: "ring-emerald-500" },
  red: { icon: "bg-rose-50 text-rose-600 dark:bg-rose-500/10 dark:text-rose-300", ring: "ring-rose-500" },
  amber: { icon: "bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-300", ring: "ring-amber-500" },
  indigo: { icon: "bg-indigo-50 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-300", ring: "ring-indigo-500" },
  violet: { icon: "bg-violet-50 text-violet-600 dark:bg-violet-500/10 dark:text-violet-300", ring: "ring-violet-500" },
} as const;

function SumCard({ label, n, sub, icon, tone, active, onClick }: {
  label: string; n: number; sub?: string; icon: ReactNode; tone: keyof typeof SUM_TONE; active: boolean; onClick: () => void;
}) {
  const t = SUM_TONE[tone];
  return (
    <button type="button" onClick={onClick}
      className={`flex flex-col gap-2 rounded-2xl border bg-white p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md dark:bg-slate-900 ${active ? `border-transparent ring-2 ${t.ring}` : "border-slate-200 dark:border-slate-800"}`}>
      <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        <span className={`flex h-7 w-7 items-center justify-center rounded-lg ${t.icon}`}>{icon}</span>{label}
      </span>
      <span className="text-2xl font-bold tabular-nums text-slate-900 dark:text-slate-50">{n}</span>
      {sub && <span className="-mt-1 text-[11px] text-slate-500">{sub}</span>}
    </button>
  );
}

function Switch({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button type="button" role="switch" aria-checked={on} onClick={() => onChange(!on)} className="inline-flex items-center gap-2">
      <span className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${on ? "bg-rose-500" : "bg-slate-300 dark:bg-slate-600"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${on ? "translate-x-4" : "translate-x-0.5"}`} />
      </span>
      <span className={`text-xs font-semibold ${on ? "text-rose-700 dark:text-rose-300" : "text-slate-500"}`}>{on ? "Yes" : "No"}</span>
    </button>
  );
}

function Toggle({ on, set, label }: { on: boolean; set: (v: boolean) => void; label: string }) {
  return (
    <button type="button" onClick={() => set(!on)}
      className={`rounded-lg px-3 py-2 text-sm font-medium ring-1 ring-inset transition ${on ? "bg-indigo-600 text-white ring-indigo-600" : "bg-white text-slate-700 ring-slate-200 hover:bg-slate-50 dark:bg-slate-900 dark:text-slate-200 dark:ring-slate-700"}`}>
      {label}
    </button>
  );
}

function FillBar({ pct, warn }: { pct: number; warn: boolean }) {
  return (
    <span className="inline-flex items-center gap-2" title={`Had a value on ${pct}% of accepted jobs`}>
      <span className="h-1.5 w-12 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <span className={`block h-full rounded-full ${warn ? "bg-amber-500" : "bg-emerald-500"}`} style={{ width: `${Math.max(3, pct)}%` }} />
      </span>
      <span className={warn ? "font-semibold text-amber-700 dark:text-amber-300" : "text-slate-600 dark:text-slate-300"}>{pct}%</span>
    </span>
  );
}
