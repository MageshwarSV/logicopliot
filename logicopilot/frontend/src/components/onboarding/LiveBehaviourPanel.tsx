import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "../ui/Button";
import * as tplApi from "../../api/onboardingTemplate";
import * as designerApi from "../../api/screenDesigner";
import type { FieldRef, LiveGroup } from "../../api/onboardingTemplate";
import type { CompositePiece } from "../../types/onboarding";
import { TemplateFieldEditor, type TemplateFieldSection } from "./TemplateFieldEditor";

// Customer Onboarding Step 4 - how ONE field behaves, edited straight on the live field it fills
// (recommended workflow, phase E: Step 4 owns all behaviour). What a mode shows:
//   full    - Document / AI-Derived / Formula fields: label, mandatory, input type, choices,
//             dropdown style, validation; plus the AI rule (with Test) or the formula pieces
//             (with a preview); plus cross-document checks for a document field.
//   extras  - Fixed / Operator Input / Reference fields, whose own panel above already sets the
//             label, mandatory and choices: dropdown style and validation only.
// Both: the safe parameter rename (moved here from the Screen Designer) and Advanced settings
// (the full field editor).

const INPUT = "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900";
const LBL = "flex flex-col gap-1 text-xs font-medium text-slate-600 dark:text-slate-300";

type Live = Record<string, unknown> & { id: string; label_name: string };
type Category = string | null;

function useLiveField(groupId: string, erpFieldId: string, reloadKey: number) {
  const [state, setState] = useState<{ ref: FieldRef | null; live: Live | null; group: LiveGroup | null; loading: boolean }>(
    { ref: null, live: null, group: null, loading: true });
  useEffect(() => {
    let on = true;
    setState((s) => ({ ...s, loading: true }));
    Promise.all([tplApi.getLinks(groupId), tplApi.getLiveGroup(groupId)]).then(([links, group]) => {
      if (!on) return;
      const ref = links.fields.find((f) => f.erp_field_id === erpFieldId)?.ref ?? null;
      let live: Live | null = null;
      if (ref?.startsWith("m:")) {
        for (const d of group.documents) {
          const m = d.marks.find((x) => x.id === ref.slice(2));
          if (m) live = m as unknown as Live;
        }
      } else if (ref?.startsWith("c:")) {
        live = (group.custom_fields ?? []).find((c) => c.id === ref.slice(2)) as unknown as Live ?? null;
      }
      setState({ ref, live, group, loading: false });
    }).catch(() => on && setState({ ref: null, live: null, group: null, loading: false }));
    return () => { on = false; };
  }, [groupId, erpFieldId, reloadKey]);
  return state;
}

export function LiveBehaviourPanel({ groupId, erpFieldId, category, mode, samples, beforeSave, onMessage }: {
  groupId: string;
  erpFieldId: string;
  category: Category;
  mode: "full" | "extras";
  /** Example values by field name (the ERP Excel's), for the formula preview. */
  samples: Record<string, string>;
  /** Live-template confirmation; false = do not save. */
  beforeSave: () => boolean;
  onMessage: (tone: "ok" | "warn", text: string) => void;
}) {
  const [reloadKey, setReloadKey] = useState(0);
  const { ref, live, group, loading } = useLiveField(groupId, erpFieldId, reloadKey);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [editor, setEditor] = useState<TemplateFieldSection | "all" | null>(null);
  useEffect(() => { setDraft({}); }, [live?.id, reloadKey]);

  if (loading) return <p className="text-xs text-slate-400">Loading the live field…</p>;
  if (!ref || !live) {
    return (
      <p className="rounded-lg border border-dashed border-slate-300 px-3 py-2 text-xs text-slate-500 dark:border-slate-600">
        Not on the live template yet - {category === "document" ? "draw its box in Step 2" : "use Create or Link in the live-template panel below"}, then set how it behaves here.
      </p>
    );
  }

  const isMark = ref.startsWith("m:");
  const v = (k: string) => (k in draft ? draft[k] : live[k]);
  const set = (k: string, val: unknown) => setDraft((d) => ({ ...d, [k]: val }));
  const dirty = Object.keys(draft).filter((k) => JSON.stringify(draft[k] ?? null) !== JSON.stringify(live[k] ?? null));

  async function save() {
    if (!dirty.length || !beforeSave()) return;
    setSaving(true);
    const patch = Object.fromEntries(dirty.map((k) => [k, draft[k]]));
    try {
      if (isMark) await tplApi.patchMark(live!.id, patch); else await tplApi.patchCustomField(live!.id, patch);
      onMessage("ok", `Saved on the live template field "${live!.label_name}".`);
      setReloadKey((n) => n + 1);
    } catch (e) {
      onMessage("warn", tplApi.apiError(e, "Could not save."));
    } finally { setSaving(false); }
  }

  const options = (v("fixed_options") as string[] | null) ?? [];
  const inputType = options.length ? "dropdown" : v("date_picker") ? "date" : "text";

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-indigo-100 bg-indigo-50/30 p-4 dark:border-indigo-500/20 dark:bg-indigo-500/5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">How it behaves <span className="font-normal text-slate-400">· live field {live.label_name}</span></p>
        <button type="button" className="text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-300" onClick={() => setEditor("all")}>
          Advanced settings…
        </button>
      </div>

      <RenameRow groupId={groupId} fieldRef={ref} name={live.label_name} beforeSave={beforeSave}
        onDone={(msg) => { onMessage("ok", msg); setReloadKey((n) => n + 1); }} onError={(m) => onMessage("warn", m)} />

      {mode === "full" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className={LBL}>Label on the screen
            <input className={INPUT} value={(v("display_label") as string) ?? ""} placeholder={live.label_name}
              onChange={(e) => set("display_label", e.target.value || null)} />
          </label>
          <label className={LBL}>Input
            <select className={INPUT} value={inputType} onChange={(e) => {
              const t = e.target.value;
              set("date_picker", t === "date");
              if (t !== "dropdown") set("fixed_options", null);
              else if (!options.length) set("fixed_options", ["Choice 1"]);
            }}>
              <option value="text">Text box</option>
              <option value="dropdown">Dropdown</option>
              <option value="date">Date picker</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200 sm:col-span-2">
            <input type="checkbox" checked={!!v("ask_operator_required")} onChange={(e) => set("ask_operator_required", e.target.checked)} />
            Mandatory - the job cannot be submitted while it is empty
          </label>
          {inputType === "dropdown" && (
            <label className={`${LBL} sm:col-span-2`}>Choices, one per line (CODE:Label saves the code)
              <textarea className={`${INPUT} font-mono text-xs`} rows={4} value={options.join("\n")}
                onChange={(e) => set("fixed_options", e.target.value.split("\n").map((x) => x.trim()).filter(Boolean))} />
            </label>
          )}
        </div>
      )}

      {(mode === "extras" ? (live.fixed_options as string[] | null)?.length : inputType === "dropdown") ? (
        <label className={LBL}>Dropdown style
          <select className={INPUT} value={(v("dropdown_style") as string) ?? "plain"} onChange={(e) => set("dropdown_style", e.target.value)}>
            <option value="plain">Plain list</option>
            <option value="search">Search, pick only</option>
            <option value="search_free">Search, or type anything</option>
          </select>
        </label>
      ) : null}

      <details className="rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900">
        <summary className="cursor-pointer text-xs font-medium text-slate-600 dark:text-slate-300">Validation {v("validation_pattern") || v("validation_uppercase") ? "· on" : ""}</summary>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <label className={LBL}>Pattern (regular expression)
            <input className={`${INPUT} font-mono text-xs`} value={(v("validation_pattern") as string) ?? ""} placeholder="e.g. ^[A-Z]{4}\d{7}$"
              onChange={(e) => set("validation_pattern", e.target.value || null)} />
          </label>
          <label className={LBL}>Message when it does not match
            <input className={INPUT} value={(v("validation_message") as string) ?? ""} onChange={(e) => set("validation_message", e.target.value || null)} />
          </label>
          <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
            <input type="checkbox" checked={!!v("validation_uppercase")} onChange={(e) => set("validation_uppercase", e.target.checked)} />
            Always UPPERCASE
          </label>
        </div>
      </details>

      {category === "ai" && !isMark && (
        <AiRule live={live} group={group} value={v} set={set} onMessage={onMessage} dirty={dirty.includes("ai_prompt") || dirty.includes("source_document_ids")} />
      )}
      {category === "formula" && !isMark && (
        <FormulaPieces live={live} group={group} value={v} set={set} samples={samples} />
      )}
      {category === "document" && (
        <div className="flex flex-wrap gap-3 text-xs">
          <button type="button" className="font-medium text-indigo-600 hover:underline dark:text-indigo-300" onClick={() => setEditor("crosscheck")}>
            Cross-document checks…
          </button>
          <Link to={`/super-admin/onboarding/prompts?customer=${groupId}`} className="font-medium text-indigo-600 hover:underline dark:text-indigo-300">
            Reading prompt &amp; format rule → Step 3
          </Link>
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        {dirty.length > 0 && <button type="button" className="text-xs text-slate-500 hover:underline" onClick={() => setDraft({})}>Undo</button>}
        <Button size="sm" disabled={!dirty.length} isLoading={saving} onClick={() => void save()}>
          Save{dirty.length ? ` ${dirty.length} change${dirty.length === 1 ? "" : "s"}` : ""}
        </Button>
      </div>

      <TemplateFieldEditor groupId={groupId} fieldRef={editor ? ref : null} open={!!editor}
        initialSection={editor && editor !== "all" ? editor : undefined}
        onClose={() => setEditor(null)} onSaved={() => setReloadKey((n) => n + 1)} />
    </div>
  );
}

/** The safe rename: the server checks where the name is used and refuses when something depends on it. */
function RenameRow({ groupId, fieldRef, name, beforeSave, onDone, onError }: {
  groupId: string; fieldRef: FieldRef; name: string; beforeSave: () => boolean;
  onDone: (msg: string) => void; onError: (msg: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(name);
  const [usage, setUsage] = useState<designerApi.FieldUsage | null>(null);
  useEffect(() => { setValue(name); setUsage(null); setOpen(false); }, [name, fieldRef]);
  useEffect(() => {
    if (!open) return;
    designerApi.fieldUsage(groupId, fieldRef).then(setUsage).catch(() => setUsage(null));
  }, [open, groupId, fieldRef]);
  if (!open) {
    return (
      <p className="text-xs text-slate-500">
        Parameter name <span className="font-mono text-slate-700 dark:text-slate-200">{name}</span>{" "}
        <button type="button" className="text-indigo-600 hover:underline dark:text-indigo-300" onClick={() => setOpen(true)}>rename…</button>
      </p>
    );
  }
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs dark:border-slate-700 dark:bg-slate-900">
      <div className="flex gap-2">
        <input className={`${INPUT} font-mono text-xs`} value={value} onChange={(e) => setValue(e.target.value)} />
        <Button size="sm" disabled={!usage?.allowed || !value.trim() || value.trim() === name} onClick={async () => {
          if (!beforeSave()) return;
          try {
            const r = await designerApi.renameParameter(groupId, fieldRef, value.trim());
            onDone(`Renamed to "${r.name}" (${r.renamed} place${r.renamed === 1 ? "" : "s"} updated).`);
          } catch (e) { onError(tplApi.apiError(e, "Could not rename.")); }
        }}>{usage && !usage.allowed ? "Cannot rename" : "Rename"}</Button>
        <button type="button" className="text-slate-500 hover:underline" onClick={() => setOpen(false)}>Cancel</button>
      </div>
      {usage && (
        <p className="mt-2 text-slate-500">
          {usage.blocks.length ? <>Cannot rename - used by: {usage.blocks.join("; ")}.</> : <>Updated with it: {usage.auto.length ? usage.auto.join("; ") : "nothing else"}.</>}
        </p>
      )}
    </div>
  );
}

function AiRule({ live, group, value, set, onMessage, dirty }: {
  live: Live; group: LiveGroup | null; value: (k: string) => unknown; set: (k: string, v: unknown) => void;
  onMessage: (tone: "ok" | "warn", text: string) => void; dirty: boolean;
}) {
  const [results, setResults] = useState<{ document: string; value: string | null; note: string | null }[] | null>(null);
  const [busy, setBusy] = useState(false);
  const docs = group?.documents ?? [];
  const chosen = (value("source_document_ids") as string[] | null) ?? [];
  return (
    <div className="flex flex-col gap-2">
      <label className={LBL}>AI rule - what to work out, and how
        <textarea className={INPUT} rows={4} value={(value("ai_prompt") as string) ?? ""}
          placeholder="e.g. The country the goods were made in, as a 2-letter code; if several, the one on most lines."
          onChange={(e) => set("ai_prompt", e.target.value)} />
      </label>
      <div className="text-xs text-slate-600 dark:text-slate-300">
        <p className="mb-1 font-medium">Documents it reads <span className="font-normal text-slate-400">(none ticked = all)</span></p>
        <div className="flex flex-wrap gap-2">
          {docs.map((d) => (
            <label key={d.id} className="flex items-center gap-1 rounded-full border border-slate-200 px-2 py-0.5 dark:border-slate-700">
              <input type="checkbox" checked={chosen.includes(d.id)}
                onChange={(e) => set("source_document_ids", e.target.checked ? [...chosen, d.id] : chosen.filter((x) => x !== d.id))} />
              {d.name}
            </label>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-3">
        <Button size="sm" variant="secondary" isLoading={busy} disabled={dirty} onClick={async () => {
          setBusy(true);
          try { setResults((await tplApi.testAiField(live.id)).results); }
          catch (e) { onMessage("warn", tplApi.apiError(e, "Could not test.")); }
          finally { setBusy(false); }
        }}>Test on the samples</Button>
        {dirty && <span className="text-[11px] text-slate-400">Save first - the test runs the saved rule.</span>}
      </div>
      {results && (
        <ul className="rounded-lg border border-slate-200 bg-white p-2 text-xs dark:border-slate-700 dark:bg-slate-900">
          {results.map((r) => (
            <li key={r.document} className="flex gap-2 py-0.5">
              <span className="w-36 shrink-0 text-slate-500">{r.document}</span>
              <span className={r.value ? "font-mono text-emerald-700 dark:text-emerald-300" : "text-slate-400"}>{r.value ?? (r.note || "nothing found")}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function FormulaPieces({ live, group, value, set, samples }: {
  live: Live; group: LiveGroup | null; value: (k: string) => unknown; set: (k: string, v: unknown) => void;
  samples: Record<string, string>;
}) {
  const pieces = (value("composite_source_labels") as CompositePiece[] | null) ?? [];
  const seps = (value("composite_separators") as string[] | null) ?? pieces.map(() => " ");
  const names = useMemo(() => {
    const out = new Set<string>();
    for (const d of group?.documents ?? []) for (const m of d.marks) out.add(m.label_name);
    for (const c of group?.custom_fields ?? []) if (c.id !== live.id) out.add(c.label_name);
    return [...out].sort((a, b) => a.localeCompare(b));
  }, [group, live.id]);
  const update = (p: CompositePiece[], s: string[]) => { set("composite_source_labels", p); set("composite_separators", s); };
  const preview = pieces.map((p, i) => {
    const text = typeof p === "string" ? (samples[p] ?? `[${p}]`) : p.fixed;
    return (i === 0 ? "" : seps[i] ?? " ") + text;
  }).join("");
  return (
    <div className="flex flex-col gap-2 text-xs">
      <p className="font-medium text-slate-600 dark:text-slate-300">Joined from these pieces, in order</p>
      <datalist id={`pieces-${live.id}`}>{names.map((n) => <option key={n} value={n} />)}</datalist>
      {pieces.map((p, i) => (
        <div key={i} className="flex flex-wrap items-center gap-2">
          {i > 0 && (
            <input className="w-16 rounded border border-slate-300 px-1.5 py-1 font-mono dark:border-slate-700 dark:bg-slate-900" value={seps[i] ?? " "}
              title="Text put before this piece" onChange={(e) => { const s = [...seps]; s[i] = e.target.value; update(pieces, s); }} />
          )}
          {typeof p === "string" ? (
            <input list={`pieces-${live.id}`} className="min-w-[12rem] flex-1 rounded border border-slate-300 px-2 py-1 dark:border-slate-700 dark:bg-slate-900" value={p}
              onChange={(e) => { const n = [...pieces]; n[i] = e.target.value; update(n, seps); }} />
          ) : (
            <input className="min-w-[12rem] flex-1 rounded border border-amber-300 px-2 py-1 dark:border-amber-600 dark:bg-slate-900" value={p.fixed}
              placeholder="Fixed text" onChange={(e) => { const n = [...pieces]; n[i] = { fixed: e.target.value }; update(n, seps); }} />
          )}
          <button type="button" className="text-rose-500 hover:underline" onClick={() => update(pieces.filter((_, j) => j !== i), seps.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <div className="flex gap-3">
        <button type="button" className="font-medium text-indigo-600 hover:underline dark:text-indigo-300" onClick={() => update([...pieces, ""], [...seps, " "])}>+ Field</button>
        <button type="button" className="font-medium text-indigo-600 hover:underline dark:text-indigo-300" onClick={() => update([...pieces, { fixed: "" }], [...seps, " "])}>+ Fixed text</button>
      </div>
      {pieces.length > 0 && (
        <p className="rounded-lg bg-white px-2 py-1.5 font-mono text-slate-700 dark:bg-slate-900 dark:text-slate-200">
          Preview: {preview || "—"} <span className="font-sans text-slate-400">(example values from the ERP Excel; [name] = no example)</span>
        </p>
      )}
    </div>
  );
}
