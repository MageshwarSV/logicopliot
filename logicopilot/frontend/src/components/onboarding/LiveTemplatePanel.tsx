import { useEffect, useMemo, useState } from "react";
import { Button } from "../ui/Button";
import { TemplateFieldEditor } from "./TemplateFieldEditor";
import {
  apiError,
  createTemplateField,
  getExcelMapping,
  getLinks,
  getLiveGroup,
  linkToLiveField,
  setColumnRule,
  setSheetPlan,
  type ExcelColumnRule,
  type ExcelSheetPlan,
  type FieldLink,
  type PickBy,
} from "../../api/onboardingTemplate";

// "Live template" panel for one onboarding field (an ERP Excel column / ERP UI field):
// which live template field it fills, link / create it, open every setting of that field, and
// the ERP Excel column's own rules (Customer Template Mapping) - all saved on the live template.

const INPUT =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100";
const LABEL = "mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-300";
const HELP = "mt-1 text-[11px] leading-snug text-slate-500 dark:text-slate-400";
const ERP_UI_SHEET = "ERP UI (not in Excel)";

const CATEGORY_KIND: Record<string, string> = {
  fixed: "Fixed value", operator: "Operator input", reference: "Reference lookup", ai: "AI works it out", formula: "Join other fields",
};

export function LiveTemplatePanel({
  groupId,
  erpFieldId,
  compact = false,
  onChanged,
}: {
  groupId: string;
  erpFieldId: string;
  /** Collapsed by default when shown inside another form. */
  compact?: boolean;
  onChanged?: () => void;
}) {
  const [link, setLink] = useState<FieldLink | null>(null);
  const [labels, setLabels] = useState<string[]>([]);
  const [special, setSpecial] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(!compact);
  const [pickLabel, setPickLabel] = useState("");

  async function load() {
    try {
      const [l, g] = await Promise.all([getLinks(groupId), getLiveGroup(groupId)]);
      const mine = l.fields.find((f) => f.erp_field_id === erpFieldId) ?? null;
      setLink(mine);
      setSpecial(new Set(l.special_names.map((s) => s.name)));
      const s = new Set<string>();
      g.documents.forEach((d) => d.marks.forEach((m) => s.add(m.label_name)));
      (g.custom_fields ?? []).forEach((c) => s.add(c.label_name));
      setLabels([...s].sort((a, b) => a.localeCompare(b)));
      setPickLabel(mine?.suggested_label ?? "");
    } catch (e) {
      setNote(apiError(e, "Could not read the live template."));
    }
  }

  useEffect(() => { void load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [groupId, erpFieldId]);

  async function doLink() {
    if (!pickLabel) return;
    setBusy(true);
    try {
      await linkToLiveField(erpFieldId, pickLabel);
      setNote(`Linked: this column now fills "${pickLabel}".`);
      await load();
      onChanged?.();
    } catch (e) { setNote(apiError(e, "Could not link.")); } finally { setBusy(false); }
  }

  async function doCreate() {
    setBusy(true);
    try {
      const r = await createTemplateField(erpFieldId);
      setNote(r.created ? `Created "${r.label}" on the live template and linked it.` : `Linked to the existing "${r.label}".`);
      await load();
      onChanged?.();
      setEditorOpen(true);
    } catch (e) { setNote(apiError(e, "Could not create the field.")); } finally { setBusy(false); }
  }

  if (!link) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 p-3 text-xs text-slate-500 dark:border-slate-700">
        {note ?? "Reading the live template…"}
      </div>
    );
  }

  const category = link.category ?? "";
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-indigo-200 bg-indigo-50/40 p-3 dark:border-indigo-500/30 dark:bg-indigo-500/5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-indigo-700 dark:text-indigo-300">Live template</span>
        {link.ref ? (
          <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300">
            Fills "{link.label}" {link.ref.startsWith("m:") ? "· box on a document" : "· custom field"}
          </span>
        ) : (
          <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">
            Not connected to the live template yet
          </span>
        )}
        {link.label && special.has(link.label) && (
          <span title="The app has built-in behaviour for this exact name - keep it spelled exactly."
            className="rounded-full bg-violet-100 px-2.5 py-0.5 text-xs font-medium text-violet-800 dark:bg-violet-500/15 dark:text-violet-300">
            built-in name
          </span>
        )}
        {link.ref && (
          <Button size="sm" className="ml-auto" onClick={() => setEditorOpen(true)}>All field settings</Button>
        )}
      </div>

      {!link.ref && (
        <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto]">
          <select className={INPUT} value={pickLabel} onChange={(e) => setPickLabel(e.target.value)}>
            <option value="">— link to an existing live field —</option>
            {labels.map((l) => <option key={l} value={l}>{l}{special.has(l) ? "  ★" : ""}</option>)}
          </select>
          <Button size="sm" variant="secondary" onClick={doLink} disabled={!pickLabel || busy}>Link</Button>
          {category === "document" ? (
            <span className="self-center text-[11px] text-slate-500">or draw its box in Step 2 to create it</span>
          ) : (
            <Button size="sm" onClick={doCreate} isLoading={busy} disabled={!category}>
              Create {CATEGORY_KIND[category] ? `(${CATEGORY_KIND[category]})` : "field"}
            </Button>
          )}
          {link.suggested_label && <p className={`${HELP} sm:col-span-3`}>Suggested by name: "{link.suggested_label}".</p>}
          {!category && <p className={`${HELP} sm:col-span-3`}>Classify the field first (Step 1) to create it.</p>}
        </div>
      )}

      {link.sheet !== ERP_UI_SHEET && (
        <div>
          <button type="button" className="text-xs font-semibold text-indigo-700 hover:underline dark:text-indigo-300" onClick={() => setRulesOpen((o) => !o)}>
            {rulesOpen ? "▾" : "▸"} ERP Excel column rules · {link.sheet}{link.column ? ` · column ${link.column}` : ""}
          </button>
          {rulesOpen && (
            <ColumnRules groupId={groupId} link={link} labels={labels}
              onSaved={async () => { await load(); onChanged?.(); }} />
          )}
        </div>
      )}

      {note && <p className={HELP}>{note}</p>}
      <TemplateFieldEditor
        groupId={groupId}
        fieldRef={link.ref}
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        onSaved={() => { void load(); onChanged?.(); }}
        heading={<span>ERP Excel: {link.sheet} · {link.excel_name}</span>}
      />
    </div>
  );
}

// ---- one Excel column's rules ---------------------------------------------------------------------

function ColumnRules({ groupId, link, labels, onSaved }: {
  groupId: string; link: FieldLink; labels: string[]; onSaved: () => Promise<void> | void;
}) {
  const [plan, setPlan] = useState<ExcelSheetPlan | null | undefined>(undefined);
  const [meta, setMeta] = useState<{ transforms: string[]; scopes: string[]; documents: string[] }>({ transforms: [], scopes: [], documents: [] });
  const [rule, setRule] = useState<ExcelColumnRule>({});
  const [sheetDraft, setSheetDraft] = useState<{ scope?: string; header_row?: number }>({});
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function load() {
    const m = await getExcelMapping(groupId);
    setMeta({ transforms: m.transforms, scopes: m.scopes, documents: m.documents });
    const p = m.sheets.find((s) => s.sheet === link.sheet) ?? null;
    setPlan(p);
    const col = p?.columns.find((c) => (link.column && (c.column ?? "").toUpperCase() === link.column.toUpperCase()))
      ?? p?.columns.find((c) => (c.header ?? "").trim().toLowerCase() === link.excel_name.trim().toLowerCase());
    const { column: _c, header: _h, ...r } = col ?? ({} as Record<string, unknown>);
    void _c; void _h;
    setRule(r as ExcelColumnRule);
    setSheetDraft({});
  }
  useEffect(() => { void load().catch((e) => setNote(apiError(e, "Could not read the mapping."))); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [groupId, link.erp_field_id]);

  const original = useMemo(() => JSON.stringify(rule), [plan]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    setSaving(true);
    setNote(null);
    try {
      if (plan === null) {
        await setSheetPlan(groupId, { sheet: link.sheet, scope: sheetDraft.scope ?? "job", header_row: sheetDraft.header_row ?? 1 });
      } else if (sheetDraft.scope || sheetDraft.header_row) {
        await setSheetPlan(groupId, { sheet: link.sheet, ...sheetDraft });
      }
      const payload: Record<string, unknown> = {};
      (["field", "value", "values", "pick_by", "from_document", "fallback_fields", "transform", "max_len"] as const).forEach((k) => {
        const val = rule[k];
        payload[k] = val === undefined || val === "" || (Array.isArray(val) && !val.length) ? null : val;
      });
      await setColumnRule(groupId, { sheet: link.sheet, column: link.column, header: link.excel_name, rule: payload });
      setNote("Column rules saved on the live template.");
      await load();
      await onSaved();
    } catch (e) {
      setNote(apiError(e, "Could not save the column."));
    } finally {
      setSaving(false);
    }
  }

  if (plan === undefined) return <p className={HELP}>Reading the mapping…</p>;

  const pick: PickBy | undefined = rule.pick_by;
  const fieldValue = rule.field ?? "";
  const set = (patch: Partial<ExcelColumnRule>) => setRule((r) => ({ ...r, ...patch }));
  const changed = JSON.stringify(rule) !== original || Object.keys(sheetDraft).length > 0 || plan === null;

  return (
    <div className="mt-2 flex flex-col gap-3">
      <div className="grid gap-2 sm:grid-cols-3">
        <div>
          <span className={LABEL}>Sheet rows</span>
          <select className={INPUT} value={sheetDraft.scope ?? plan?.scope ?? "job"} onChange={(e) => setSheetDraft((s) => ({ ...s, scope: e.target.value }))}>
            <option value="job">One row for the job</option>
            <option value="line">One row per product line</option>
            <option value="invoice">One row per invoice</option>
            <option value="fixed">Fixed block (values per row)</option>
          </select>
        </div>
        <div>
          <span className={LABEL}>Headings on row</span>
          <input className={INPUT} inputMode="numeric" value={sheetDraft.header_row ?? plan?.header_row ?? 1}
            onChange={(e) => setSheetDraft((s) => ({ ...s, header_row: Math.max(1, Number(e.target.value.replace(/\D/g, "")) || 1) }))} />
        </div>
        {plan === null && <p className={`${HELP} self-end`}>This sheet is not in the mapping yet - saving adds it.</p>}
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <span className={LABEL}>Reads</span>
          <select className={INPUT} value={pick ? "__pick" : fieldValue} onChange={(e) => {
            const val = e.target.value;
            if (val === "__pick") set({ pick_by: { field: labels[0] ?? "", options: {} }, field: "" });
            else set({ field: val, pick_by: undefined });
          }}>
            <option value="">— nothing (fixed value / blank) —</option>
            <option value="#row">Row number (1, 2, 3…)</option>
            <option value="#invoice">Invoice number of the row</option>
            <option value="__pick">Choose between fields…</option>
            {labels.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </div>
        <div>
          <span className={LABEL}>Fixed value</span>
          <input className={INPUT} value={rule.value ?? ""} onChange={(e) => set({ value: e.target.value })} placeholder="e.g. + or G or 0.00" />
          <p className={HELP}>Used for every row; with a field it is the last fallback. [[Field]] inserts a field's value.</p>
        </div>
      </div>

      {pick && (
        <div className="rounded-lg border border-slate-200 p-2 dark:border-slate-700">
          <span className={LABEL}>Choose between fields</span>
          <div className="grid gap-2 sm:grid-cols-2">
            <select className={INPUT} value={pick.field} onChange={(e) => set({ pick_by: { ...pick, field: e.target.value } })}>
              {labels.map((l) => <option key={l} value={l}>The value of: {l}</option>)}
            </select>
            <select className={INPUT} value={pick.default ?? ""} onChange={(e) => set({ pick_by: { ...pick, default: e.target.value || undefined } })}>
              <option value="">— no default —</option>
              {Object.keys(pick.options).map((k) => <option key={k} value={k}>Default: {k}</option>)}
            </select>
          </div>
          {Object.entries(pick.options).map(([k, target], i) => (
            <div key={i} className="mt-2 grid grid-cols-[1fr_auto_1fr_auto] items-center gap-2">
              <input className={INPUT} value={k} placeholder="When it is…" onChange={(e) => {
                const entries = Object.entries(pick.options); entries[i] = [e.target.value, target];
                set({ pick_by: { ...pick, options: Object.fromEntries(entries) } });
              }} />
              <span className="text-xs text-slate-400">→ read</span>
              <select className={INPUT} value={target} onChange={(e) => set({ pick_by: { ...pick, options: { ...pick.options, [k]: e.target.value } } })}>
                {labels.map((l) => <option key={l} value={l}>{l}</option>)}
              </select>
              <button type="button" className="text-slate-400 hover:text-rose-600" onClick={() => {
                const { [k]: _gone, ...rest } = pick.options; void _gone; set({ pick_by: { ...pick, options: rest } });
              }}>✕</button>
            </div>
          ))}
          <Button size="sm" variant="secondary" className="mt-2" onClick={() => set({ pick_by: { ...pick, options: { ...pick.options, [`choice ${Object.keys(pick.options).length + 1}`]: labels[0] ?? "" } } })}>
            + Choice
          </Button>
        </div>
      )}

      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <span className={LABEL}>Read from one document only</span>
          <select className={INPUT} value={rule.from_document ?? ""} onChange={(e) => set({ from_document: e.target.value || undefined })} disabled={Boolean(pick)}>
            <option value="">— any document —</option>
            {meta.documents.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
          <p className={HELP}>e.g. No_of_Pkg only from the Air Waybill, never an invoice's copy.</p>
        </div>
        <div>
          <span className={LABEL}>If empty, try these fields (in order)</span>
          <select className={INPUT} value="" onChange={(e) => e.target.value && set({ fallback_fields: [...(rule.fallback_fields ?? []), e.target.value] })}>
            <option value="">+ add a fallback field</option>
            {labels.filter((l) => !(rule.fallback_fields ?? []).includes(l)).map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
          <div className="mt-1 flex flex-wrap gap-1">
            {(rule.fallback_fields ?? []).map((f, i) => (
              <span key={f} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs dark:bg-slate-800">
                {i + 1}. {f}
                <button type="button" className="text-slate-400 hover:text-rose-600" onClick={() => set({ fallback_fields: (rule.fallback_fields ?? []).filter((x) => x !== f) })}>✕</button>
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-3">
        <div>
          <span className={LABEL}>Transform</span>
          <select className={INPUT} value={rule.transform ?? ""} onChange={(e) => set({ transform: e.target.value || undefined })}>
            <option value="">— none —</option>
            {meta.transforms.map((t) => <option key={t} value={t}>{t === "leading_digits" ? "Leading digits (40HC → 40)" : t}</option>)}
          </select>
        </div>
        <div>
          <span className={LABEL}>Max length</span>
          <input className={INPUT} inputMode="numeric" value={rule.max_len ?? ""} onChange={(e) => set({ max_len: e.target.value ? Number(e.target.value.replace(/\D/g, "")) : undefined })} />
        </div>
        <div>
          <span className={LABEL}>A different value per row</span>
          <textarea className={`${INPUT} font-mono text-xs`} rows={2} placeholder="one per line"
            value={(rule.values ?? []).join("\n")} onChange={(e) => set({ values: e.target.value ? e.target.value.split("\n") : undefined })} />
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} isLoading={saving} disabled={!changed}>Save column rules</Button>
        {note && <span className={HELP}>{note}</span>}
      </div>
    </div>
  );
}
