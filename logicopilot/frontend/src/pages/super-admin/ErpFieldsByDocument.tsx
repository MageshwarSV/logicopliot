/** ERP Field Configuration - "By document" view (2026-11-28): one row per field, the way the
 *  operator sees it (document -> field label), with where it goes in the ERP Excel (a field can
 *  feed two columns), whether it is mandatory, the validation in force, what went wrong and what
 *  to change. Changes go into the page's shared draft and are saved by its save bar. */
import { Fragment, useMemo, useState } from "react";
import type { ErpRule, FieldItem, FieldView } from "../../api/erpRules";
import { ChevronDownIcon, DocumentIcon, ExclamationTriangleIcon, SearchIcon, SparklesIcon } from "../../components/ui/icons";

type Props = {
  view: FieldView;
  draft: Record<string, ErpRule>;
  setRule: (key: string, patch: ErpRule) => void;
  editColumn: (key: string) => void;
};

const GROUP_TONE: Record<FieldItem["kind"], string> = {
  document: "bg-sky-50 text-sky-800 dark:bg-sky-500/10 dark:text-sky-300",
  job: "bg-indigo-50 text-indigo-800 dark:bg-indigo-500/10 dark:text-indigo-300",
  missing: "bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-300",
};

export function ErpFieldsByDocument({ view, draft, setRule, editColumn }: Props) {
  const [q, setQ] = useState("");
  const [doc, setDoc] = useState("");
  const [only, setOnly] = useState<"" | "issues" | "suggestions" | "mandatory" | "not_mandatory">("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const isMand = (key: string, saved: boolean) => (draft[key] ? !!draft[key].required && !draft[key].off : saved);
  const fieldMand = (f: FieldItem) => {
    const flags = f.targets.map((t) => isMand(t.key, t.mandatory));
    return flags.every(Boolean) ? "all" : flags.some(Boolean) ? "some" : "none";
  };
  const groups = useMemo(() => Array.from(new Set(view.fields.map((f) => f.group))), [view.fields]);

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    return view.fields.filter((f) => {
      if (doc && f.group !== doc) return false;
      if (s && !`${f.display} ${f.label} ${f.group} ${f.targets.map((t) => `${t.sheet} ${t.header}`).join(" ")}`.toLowerCase().includes(s)) return false;
      const m = fieldMand(f);
      if (only === "issues" && !f.issues.length) return false;
      if (only === "suggestions" && !f.suggestions.some((x) => x.kind !== "covered" && !(x.kind === "mandatory" && m === "all"))) return false;
      if (only === "mandatory" && m === "none") return false;
      if (only === "not_mandatory" && m !== "none") return false;
      return true;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.fields, q, doc, only, draft]);

  const byGroup = useMemo(() => {
    const out: { group: string; kind: FieldItem["kind"]; fields: FieldItem[] }[] = [];
    for (const f of shown) {
      const last = out[out.length - 1];
      if (last && last.group === f.group) last.fields.push(f);
      else out.push({ group: f.group, kind: f.kind, fields: [f] });
    }
    return out;
  }, [shown]);

  const sel = "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm dark:border-slate-700 dark:bg-slate-900";
  let sno = 0;

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 p-4 dark:border-slate-800">
        <div className="relative min-w-[14rem] flex-1">
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search a field or ERP column…" className={`${sel} w-full pl-9`} />
        </div>
        <select value={doc} onChange={(e) => setDoc(e.target.value)} className={sel} aria-label="Document">
          <option value="">All documents</option>
          {groups.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
        <select value={only} onChange={(e) => setOnly(e.target.value as typeof only)} className={sel} aria-label="Show">
          <option value="">All fields</option>
          <option value="issues">Fields with issues ({view.counts.with_issues})</option>
          <option value="suggestions">Fields with a suggestion</option>
          <option value="mandatory">Mandatory</option>
          <option value="not_mandatory">Not mandatory</option>
        </select>
        <span className="ml-auto text-xs text-slate-500">{shown.length} of {view.fields.length} fields</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10 bg-slate-50 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:bg-slate-800/80 dark:text-slate-400">
            <tr>
              <th className="w-12 px-3 py-3 text-center">S.No</th>
              <th className="px-3 py-3">Field (as the operator sees it)</th>
              <th className="px-3 py-3">Sent to ERP (sheet · column)</th>
              <th className="px-3 py-3">Mandatory</th>
              <th className="px-3 py-3">Current validation</th>
              <th className="px-3 py-3">Issues seen</th>
              <th className="px-3 py-3">Suggestion</th>
            </tr>
          </thead>
          <tbody>
            {byGroup.length === 0 && (
              <tr><td colSpan={7} className="p-10 text-center text-slate-500">No fields match these filters.</td></tr>
            )}
            {byGroup.map((g) => {
              const isCollapsed = !!collapsed[g.group];
              const issues = g.fields.filter((f) => f.issues.length).length;
              return (
                <Fragment key={g.group}>
                  <tr className="border-y border-slate-200 bg-slate-100/70 dark:border-slate-700 dark:bg-slate-800/60">
                    <td colSpan={7} className="px-3 py-2">
                      <button type="button" onClick={() => setCollapsed((c) => ({ ...c, [g.group]: !c[g.group] }))} className="flex w-full items-center gap-3 text-left">
                        <ChevronDownIcon className={`h-4 w-4 text-slate-500 transition-transform ${isCollapsed ? "-rotate-90" : ""}`} />
                        <span className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-sm font-bold ${GROUP_TONE[g.kind]}`}>
                          {g.kind === "missing" ? <ExclamationTriangleIcon className="h-4 w-4" /> : <DocumentIcon className="h-4 w-4" />}{g.group}
                        </span>
                        <span className="text-xs text-slate-500">{g.fields.length} field(s)</span>
                        {issues > 0 && <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[11px] font-semibold text-rose-700 dark:bg-rose-500/20 dark:text-rose-300">{issues} with issues</span>}
                      </button>
                    </td>
                  </tr>
                  {!isCollapsed && g.fields.map((f) => {
                    sno += 1;
                    const m = fieldMand(f);
                    const setAll = (on: boolean) => f.targets.forEach((t) => setRule(t.key, { required: on, off: false }));
                    return (
                      <tr key={f.label} className="border-b border-slate-100 align-top hover:bg-indigo-50/30 dark:border-slate-800 dark:hover:bg-slate-800/40">
                        <td className="px-3 py-3 text-center text-xs tabular-nums text-slate-400">{sno}</td>
                        <td className="px-3 py-3">
                          <p className="font-semibold text-slate-900 dark:text-slate-50">{f.display}</p>
                          {f.display !== f.label && <p className="text-[11px] text-slate-500">field: {f.label}</p>}
                          {f.per_line && <span className="mt-1 inline-block rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">one value per product line</span>}
                        </td>
                        <td className="px-3 py-3">
                          <ul className="space-y-1">
                            {f.targets.map((t) => (
                              <li key={t.key} className="flex items-center gap-1.5 text-xs">
                                <span className="rounded bg-slate-100 px-1.5 py-0.5 font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">{t.sheet}</span>
                                {t.column && <span className="font-mono text-[11px] text-slate-400">{t.column}</span>}
                                <button type="button" onClick={() => editColumn(t.key)} title="Open this column's rules"
                                  className="font-medium text-slate-800 hover:text-indigo-600 hover:underline dark:text-slate-100">{t.header}</button>
                              </li>
                            ))}
                          </ul>
                          {f.targets.length > 1 && <p className="mt-1 text-[10px] text-slate-500">Sent to {f.targets.length} places</p>}
                        </td>
                        <td className="px-3 py-3">
                          <button type="button" role="switch" aria-checked={m !== "none"} onClick={() => setAll(m !== "all")} className="inline-flex items-center gap-2">
                            <span className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${m === "all" ? "bg-rose-500" : m === "some" ? "bg-rose-300" : "bg-slate-300 dark:bg-slate-600"}`}>
                              <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${m !== "none" ? "translate-x-4" : "translate-x-0.5"}`} />
                            </span>
                            <span className={`text-xs font-semibold ${m !== "none" ? "text-rose-700 dark:text-rose-300" : "text-slate-500"}`}>
                              {m === "all" ? "Yes" : m === "some" ? "Partly" : "No"}
                            </span>
                          </button>
                        </td>
                        <td className="max-w-[16rem] px-3 py-3">
                          {f.validation.length
                            ? <div className="flex flex-wrap gap-1">{f.validation.map((v, n) => <span key={n} className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-700 dark:bg-slate-800 dark:text-slate-200">{v.replace(/^Built in: /, "✓ ")}</span>)}</div>
                            : <span className="text-xs text-slate-400">No validation</span>}
                        </td>
                        <td className="max-w-[18rem] px-3 py-3">
                          {f.issues.length
                            ? <ul className="space-y-1">{f.issues.map((i, n) => <li key={n} className="text-xs text-rose-700 dark:text-rose-300">• {i}</li>)}</ul>
                            : <span className="text-xs text-emerald-600 dark:text-emerald-400">No issues seen</span>}
                        </td>
                        <td className="max-w-[20rem] px-3 py-3">
                          <ul className="space-y-1.5">
                            {f.suggestions.filter((s) => !(s.kind === "mandatory" && m === "all")).map((s, n) => (
                              <li key={n} className={`text-xs ${s.kind === "covered" ? "text-emerald-700 dark:text-emerald-300" : s.strong ? "text-indigo-800 dark:text-indigo-200" : "text-slate-600 dark:text-slate-300"}`}>
                                {s.kind === "covered" ? "✓ " : <SparklesIcon className="mr-1 inline h-3 w-3" />}{s.text}
                                {s.kind === "mandatory" && (
                                  <button type="button" onClick={() => s.keys.forEach((k) => setRule(k, { required: true, off: false }))}
                                    className="ml-1.5 rounded bg-indigo-600 px-1.5 py-0.5 text-[10px] font-semibold text-white hover:bg-indigo-500">Make mandatory</button>
                                )}
                                {s.kind === "rule" && s.rule && (
                                  <button type="button" onClick={() => s.keys.forEach((k) => setRule(k, { ...s.rule }))}
                                    className="ml-1.5 rounded bg-indigo-600 px-1.5 py-0.5 text-[10px] font-semibold text-white hover:bg-indigo-500">Apply</button>
                                )}
                              </li>
                            ))}
                            {!f.suggestions.filter((s) => !(s.kind === "mandatory" && m === "all")).length && <li className="text-xs text-slate-400">Nothing to change</li>}
                          </ul>
                        </td>
                      </tr>
                    );
                  })}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {view.fixed.length > 0 && (
        <details className="border-t border-slate-100 p-4 text-sm dark:border-slate-800">
          <summary className="cursor-pointer font-semibold text-slate-700 dark:text-slate-200">Fixed values sent to the ERP ({view.fixed.length})</summary>
          <ul className="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
            {view.fixed.map((x) => (
              <li key={x.key} className="text-xs text-slate-600 dark:text-slate-300">
                <span className="font-semibold">{x.sheet}</span> · {x.header} = <span className="font-mono">{x.value ?? "—"}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
