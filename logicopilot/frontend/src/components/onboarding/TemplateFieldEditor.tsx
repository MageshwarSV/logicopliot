import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Modal } from "../ui/Modal";
import { Button } from "../ui/Button";
import * as onboardingApi from "../../api/onboarding";
import * as reviewsApi from "../../api/reviews";
import {
  apiError,
  getLiveGroup,
  getPromptHistory,
  patchCustomField,
  patchMark,
  type FieldRef,
  type LiveCustomField,
  type LiveGroup,
  type LiveMark,
  type PromptHistoryRow,
} from "../../api/onboardingTemplate";
import type { CompositePiece, CrossDocLink } from "../../types/onboarding";
import { MARK_COLORS } from "../../types/onboarding";
import { DATE_FORMAT_CHOICES } from "../../utils/fieldDates";
import { NUMBER_PATTERN } from "../../utils/fieldNumbers";
import { FixPromptModal } from "../../pages/super-admin/FixPromptModal";
import type { CorrectedField } from "../../api/extractionCorrections";

// Every setting of ONE live template field (a box drawn on a document, or a custom field), in
// one place - the same data the old Document Templates / Data Transformation screens edit,
// saved through the same routes. Opened from Customer Onboarding Steps 1, 2 and 4.

type Draft = Record<string, unknown>;
export type TemplateFieldSection =
  | "name" | "reading" | "kind" | "value" | "operator" | "table" | "reference"
  | "crosscheck" | "pairing" | "screen" | "history";
type Section = TemplateFieldSection;

type ValidationMode = "none" | "letters" | "digits" | "number" | "alnum" | "custom";
const CHAR_CLASS: Record<"letters" | "digits" | "alnum", string> = { letters: "A-Za-z", digits: "0-9", alnum: "A-Za-z0-9" };

function buildPattern(mode: ValidationMode, length: string, custom: string): string | null {
  if (mode === "none") return null;
  if (mode === "custom") return custom.trim() || null;
  if (mode === "number") return NUMBER_PATTERN;
  const len = length.trim();
  return len ? `[${CHAR_CLASS[mode]}]{${len}}` : `[${CHAR_CLASS[mode]}]+`;
}

function parsePattern(p: string | null | undefined): { mode: ValidationMode; length: string; custom: string } {
  if (!p) return { mode: "none", length: "", custom: "" };
  if (p === NUMBER_PATTERN) return { mode: "number", length: "", custom: "" };
  const m = /^\[(A-Za-z0-9|A-Za-z|0-9)\](?:\{(\d+)\}|(\+))$/.exec(p);
  if (m) return { mode: m[1] === "A-Za-z0-9" ? "alnum" : m[1] === "A-Za-z" ? "letters" : "digits", length: m[2] ?? "", custom: "" };
  return { mode: "custom", length: "", custom: p };
}

const INPUT =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 dark:focus:ring-indigo-500/20";
const AREA = `${INPUT} font-mono text-xs leading-relaxed`;
const LABEL = "mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-300";
const HELP = "mt-1 text-[11px] leading-snug text-slate-500 dark:text-slate-400";

function Row({ label, help, children }: { label: string; help?: ReactNode; children: ReactNode }) {
  return (
    <div>
      <span className={LABEL}>{label}</span>
      {children}
      {help && <p className={HELP}>{help}</p>}
    </div>
  );
}

function Check({ checked, onChange, label, help }: { checked: boolean; onChange: (v: boolean) => void; label: string; help?: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800/60">
      <input type="checkbox" className="mt-0.5 h-4 w-4 accent-indigo-600" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>
        <span className="font-medium text-slate-800 dark:text-slate-100">{label}</span>
        {help && <span className="block text-[11px] text-slate-500 dark:text-slate-400">{help}</span>}
      </span>
    </label>
  );
}

const asStr = (v: unknown) => (v == null ? "" : String(v));
const nullIfBlank = (v: string) => (v.trim() ? v : null);

export function TemplateFieldEditor({
  groupId,
  fieldRef,
  open,
  onClose,
  onSaved,
  heading,
  initialSection,
}: {
  groupId: string;
  fieldRef: FieldRef | null;
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
  /** Shown under the title, e.g. which ERP Excel column this field fills. */
  heading?: ReactNode;
  /** Open on this section (e.g. "crosscheck" from Add a cross-document check). */
  initialSection?: TemplateFieldSection;
}) {
  const [group, setGroup] = useState<LiveGroup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [saving, setSaving] = useState(false);
  const [section, setSection] = useState<Section>("name");
  const [vmode, setVmode] = useState<ReturnType<typeof parsePattern>>({ mode: "none", length: "", custom: "" });
  const [fixOpen, setFixOpen] = useState(false);

  const kind = fieldRef?.startsWith("m:") ? "mark" : "cf";
  const id = fieldRef ? fieldRef.slice(2) : "";

  async function reload() {
    try {
      const g = await getLiveGroup(groupId);
      setGroup(g);
      setError(null);
      return g;
    } catch (e) {
      setError(apiError(e, "Could not load the template."));
      return null;
    }
  }

  useEffect(() => {
    if (!open || !fieldRef) return;
    setDraft({});
    setMessage(null);
    setSection(initialSection ?? "name");
    void reload().then((g) => {
      if (!g) return;
      const f = kind === "mark"
        ? g.documents.flatMap((d) => d.marks).find((m) => m.id === id)
        : (g.custom_fields ?? []).find((c) => c.id === id);
      setVmode(parsePattern(f?.validation_pattern ?? null));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, fieldRef, groupId]);

  const mark: LiveMark | undefined = useMemo(
    () => (kind === "mark" ? group?.documents.flatMap((d) => d.marks).find((m) => m.id === id) : undefined),
    [group, id, kind],
  );
  const cf: LiveCustomField | undefined = useMemo(
    () => (kind === "cf" ? (group?.custom_fields ?? []).find((c) => c.id === id) : undefined),
    [group, id, kind],
  );
  const original = (mark ?? cf) as Record<string, unknown> | undefined;
  const doc = mark ? group?.documents.find((d) => d.id === mark.document_id) : undefined;
  const v = (key: string) => (key in draft ? draft[key] : original?.[key]);
  const set = (key: string, value: unknown) => setDraft((d) => ({ ...d, [key]: value }));
  const dirty = Object.keys(draft).length > 0;
  const labelName = asStr(v("label_name"));

  const allLabels = useMemo(() => {
    if (!group) return [] as string[];
    const s = new Set<string>();
    group.documents.forEach((d) => d.marks.forEach((m) => s.add(m.label_name)));
    (group.custom_fields ?? []).forEach((c) => s.add(c.label_name));
    return [...s].sort((a, b) => a.localeCompare(b));
  }, [group]);

  async function save() {
    if (!fieldRef || !dirty) return;
    setSaving(true);
    setMessage(null);
    try {
      const patch: Draft = { ...draft };
      if (kind === "mark") await patchMark(id, patch);
      else await patchCustomField(id, patch);
      setDraft({});
      await reload();
      setMessage("Saved to the live template.");
      onSaved?.();
    } catch (e) {
      setMessage(apiError(e, "Could not save."));
    } finally {
      setSaving(false);
    }
  }

  function setValidation(next: ReturnType<typeof parsePattern>) {
    setVmode(next);
    set("validation_pattern", buildPattern(next.mode, next.length, next.custom));
  }

  const cfKind = asStr(v("kind"));
  const sections: { key: Section; label: string; show: boolean }[] = [
    { key: "name", label: "Name", show: true },
    { key: "reading", label: "Reading & prompts", show: kind === "mark" },
    { key: "kind", label: "Where the value comes from", show: kind === "cf" },
    { key: "value", label: "Value type & checks", show: true },
    { key: "operator", label: "Operator", show: true },
    { key: "table", label: "Table / rows", show: true },
    { key: "reference", label: "Reference lookup", show: true },
    { key: "crosscheck", label: "Cross-document check", show: true },
    { key: "pairing", label: "Pair / sync", show: kind === "cf" },
    { key: "screen", label: "Job screen", show: true },
    { key: "history", label: "Prompt history", show: true },
  ];

  const fixField: CorrectedField | null = group && original
    ? ({
        group_id: group.id,
        template_document_id: mark ? mark.document_id : null,
        label_name: asStr(original.label_name),
        template: group.name,
        document: doc?.name ?? "Additional Details",
        tenant: "",
        jobs_corrected: 0, total_edits: 0, lines_corrected: 0, jobs_with_field: 0,
        correction_rate: null, wrong: 0, missed: 0, cleared: 0, last_seen: null, examples: [],
      } as CorrectedField)
    : null;

  return (
    <Modal open={open} onClose={onClose} title={`Template field · ${labelName || "…"}`} maxWidth="max-w-5xl">
      {error && <p className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}
      {!group || !original ? (
        <p className="py-10 text-center text-sm text-slate-500">{error ? "" : "Loading the live field…"}</p>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
            <span className="rounded-full bg-indigo-50 px-2.5 py-1 font-medium text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300">
              {kind === "mark" ? `Box on ${doc?.name ?? "a document"}` : `Custom field · ${cfKind || "hardcoded"}`}
            </span>
            {heading}
            <span className="ml-auto">Saves to the live template - jobs use it from the next extraction.</span>
          </div>

          <div className="grid gap-4 md:grid-cols-[12rem_1fr]">
            <nav className="flex flex-row flex-wrap gap-1 md:flex-col">
              {sections.filter((s) => s.show).map((s) => (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => setSection(s.key)}
                  className={`rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                    section === s.key
                      ? "bg-indigo-600 font-semibold text-white"
                      : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </nav>

            <div className="flex min-h-[22rem] flex-col gap-3">
              {section === "name" && (
                <>
                  <Row label="Parameter name (internal)" help="What the ERP Excel mapping, ERP scripts and every job's values use. Renaming also renames it in the ERP Excel mapping.">
                    <input className={INPUT} value={labelName} onChange={(e) => set("label_name", e.target.value)} />
                  </Row>
                  <Row label="Label on the job screen" help="Blank = the parameter name.">
                    <input className={INPUT} value={asStr(v("display_label"))} onChange={(e) => set("display_label", nullIfBlank(e.target.value))} />
                  </Row>
                  <Row label="Example value" help="A sample answer - shown while recording ERP scripts and to the AI as a hint.">
                    <input className={INPUT} value={asStr(v("example_value"))} onChange={(e) => set("example_value", nullIfBlank(e.target.value))} />
                  </Row>
                  {kind === "mark" && (
                    <Row label="Text around the box (anchor)" help="Captured when the box was drawn and fed to the reader. Correct it if it shows the wrong caption.">
                      <input className={INPUT} value={asStr(v("detected_anchor"))} onChange={(e) => set("detected_anchor", nullIfBlank(e.target.value))} />
                    </Row>
                  )}
                  {kind === "mark" && mark && (
                    <p className={HELP}>
                      Box: page {mark.page_number} · x {mark.x.toFixed(3)} · y {mark.y.toFixed(3)} · {mark.width.toFixed(3)} × {mark.height.toFixed(3)} - redraw it in Step 2 Document Mapping.
                    </p>
                  )}
                </>
              )}

              {section === "reading" && mark && (
                <ReadingSection mark={mark} v={v} set={set} onFix={() => setFixOpen(true)} />
              )}

              {section === "kind" && cf && <KindSection group={group} cf={cf} v={v} set={set} labels={allLabels} />}

              {section === "value" && (
                <>
                  <Row label="Input">
                    <div className="flex flex-wrap gap-2">
                      {(["text", "dropdown", "date"] as const).map((t) => {
                        const cur = v("date_picker") ? "date" : (v("fixed_options") as string[] | null)?.length ? "dropdown" : "text";
                        return (
                          <button
                            key={t}
                            type="button"
                            onClick={() => {
                              if (t === "date") { set("date_picker", true); set("fixed_options", []); }
                              else if (t === "text") { set("date_picker", false); set("fixed_options", []); }
                              else { set("date_picker", false); if (!(v("fixed_options") as string[] | null)?.length) set("fixed_options", ["Option 1"]); }
                            }}
                            className={`rounded-lg border px-3 py-1.5 text-sm ${cur === t ? "border-indigo-500 bg-indigo-50 font-semibold text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" : "border-slate-200 dark:border-slate-700"}`}
                          >
                            {t === "text" ? "Text" : t === "dropdown" ? "Dropdown list" : "Date"}
                          </button>
                        );
                      })}
                    </div>
                  </Row>
                  {(v("fixed_options") as string[] | null)?.length ? (
                    <div className="grid gap-3 sm:grid-cols-2">
                      <Row label="Choices (one per line, code:Label allowed)">
                        <textarea
                          className={AREA}
                          rows={6}
                          value={((v("fixed_options") as string[] | null) ?? []).join("\n")}
                          onChange={(e) => set("fixed_options", e.target.value.split("\n"))}
                        />
                      </Row>
                      <Row label="Dropdown style">
                        <select className={INPUT} value={asStr(v("dropdown_style")) || "plain"} onChange={(e) => set("dropdown_style", e.target.value)}>
                          <option value="plain">Plain list - pick only</option>
                          <option value="search">Searchable - pick only</option>
                          <option value="search_free">Searchable - typed value also allowed</option>
                        </select>
                      </Row>
                    </div>
                  ) : null}
                  {Boolean(v("date_picker")) && (
                    <Row label="Date format">
                      <select className={INPUT} value={asStr(v("date_format"))} onChange={(e) => set("date_format", nullIfBlank(e.target.value))}>
                        {DATE_FORMAT_CHOICES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                      </select>
                    </Row>
                  )}
                  <Row label="Validation" help="Checked on the job screen as ^(?:pattern)$ against the value.">
                    <div className="flex flex-wrap gap-2">
                      {(["none", "letters", "digits", "number", "alnum", "custom"] as ValidationMode[]).map((m) => (
                        <button
                          key={m}
                          type="button"
                          onClick={() => setValidation({ ...vmode, mode: m })}
                          className={`rounded-lg border px-3 py-1.5 text-sm ${vmode.mode === m ? "border-indigo-500 bg-indigo-50 font-semibold text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" : "border-slate-200 dark:border-slate-700"}`}
                        >
                          {m === "none" ? "No check" : m === "alnum" ? "Letters & digits" : m[0].toUpperCase() + m.slice(1)}
                        </button>
                      ))}
                    </div>
                  </Row>
                  {["letters", "digits", "alnum"].includes(vmode.mode) && (
                    <Row label="Exact length (blank = any)">
                      <input className={INPUT} inputMode="numeric" value={vmode.length} onChange={(e) => setValidation({ ...vmode, length: e.target.value.replace(/\D/g, "") })} />
                    </Row>
                  )}
                  {vmode.mode === "custom" && (
                    <Row label="Pattern (regular expression)">
                      <input className={`${INPUT} font-mono`} value={vmode.custom} onChange={(e) => setValidation({ ...vmode, custom: e.target.value })} />
                    </Row>
                  )}
                  {vmode.mode !== "none" && (
                    <Row label="Message when it fails" help="Blank = a generic message.">
                      <input className={INPUT} value={asStr(v("validation_message"))} onChange={(e) => set("validation_message", nullIfBlank(e.target.value))} />
                    </Row>
                  )}
                  <Check checked={Boolean(v("validation_uppercase"))} onChange={(b) => set("validation_uppercase", b)} label="Always UPPERCASE" help="Converted on save, whatever the operator types." />
                </>
              )}

              {section === "operator" && (
                <>
                  <Check checked={Boolean(v("ask_operator"))} onChange={(b) => set("ask_operator", b)} label="Ask the operator" help="The operator answers / confirms this on the job screen." />
                  <Row label="Required?">
                    <select className={INPUT} value={v("ask_operator_required") === false ? "optional" : "mandatory"} onChange={(e) => set("ask_operator_required", e.target.value === "mandatory")}>
                      <option value="mandatory">Mandatory - blocks the job until filled</option>
                      <option value="optional">Optional - never blocks</option>
                    </select>
                  </Row>
                  <Row label="Hint for the operator" help={'e.g. "T for Transaction, D for Deferred"'}>
                    <input className={INPUT} value={asStr(v("ask_operator_hint"))} onChange={(e) => set("ask_operator_hint", nullIfBlank(e.target.value))} />
                  </Row>
                  {kind === "cf" && (
                    <>
                      <Check checked={Boolean(v("hidden_from_capture"))} onChange={(b) => set("hidden_from_capture", b)} label="Hidden from the operator" help="Still filled and sent to the ERP, never shown on the job screen." />
                      <Row label="Only needed when this field has a value" help="Blank = always needed.">
                        <select className={INPUT} value={asStr(v("requires_value_of"))} onChange={(e) => set("requires_value_of", nullIfBlank(e.target.value))}>
                          <option value="">— always —</option>
                          {allLabels.filter((l) => l !== labelName).map((l) => <option key={l} value={l}>{l}</option>)}
                        </select>
                      </Row>
                    </>
                  )}
                </>
              )}

              {section === "table" && (
                kind === "mark" ? (
                  <>
                    <Check checked={Boolean(v("is_multi_value"))} onChange={(b) => set("is_multi_value", b)} label="Several values on this document (one per table row)" help="e.g. every product line, every container." />
                    {Boolean(v("is_multi_value")) && (
                      <>
                        <Check checked={Boolean(v("standalone_multi_value"))} onChange={(b) => set("standalone_multi_value", b)} label="Its own table" help="Rows never line up with the product lines (e.g. containers)." />
                        <Row label="Group heading of its rows">
                          <input className={INPUT} value={asStr(v("standalone_group_heading"))} onChange={(e) => set("standalone_group_heading", nullIfBlank(e.target.value))} />
                        </Row>
                      </>
                    )}
                  </>
                ) : (
                  <>
                    <Check checked={Boolean(v("per_row"))} onChange={(b) => set("per_row", b)} label="Once per product line" help="One value per line item instead of one per job (e.g. CTH, IGST SrNo)." />
                    <Check checked={Boolean(v("multi_value_from_document"))} onChange={(b) => set("multi_value_from_document", b)} label="Several values from the document" help="AI fields: return every instance found (one per row)." />
                  </>
                )
              )}

              {section === "reference" && (
                <ReferenceSection ownerType={kind === "mark" ? "marks" : "custom-fields"} ownerId={id} v={v} set={set} />
              )}

              {section === "crosscheck" && (
                <CrossCheckSection group={group} mark={mark} cf={cf} v={v} set={set} onChanged={reload} />
              )}

              {section === "pairing" && cf && (
                <>
                  <Row label="Pair with another field (picker)" help="The operator picks between the two values; the pick is written onto this field.">
                    <select className={INPUT} value={asStr(v("paired_custom_field_id"))} onChange={(e) => set("paired_custom_field_id", nullIfBlank(e.target.value))}>
                      <option value="">— not paired —</option>
                      {(group.custom_fields ?? []).filter((c) => c.id !== cf.id).map((c) => <option key={c.id} value={c.id}>{c.label_name}</option>)}
                    </select>
                  </Row>
                  <Row label="Picker heading">
                    <input className={INPUT} value={asStr(v("picker_heading"))} onChange={(e) => set("picker_heading", nullIfBlank(e.target.value))} />
                  </Row>
                  <Row label="Also set these fields to the pick (same row)">
                    <div className="grid max-h-48 gap-1 overflow-y-auto rounded-lg border border-slate-200 p-2 dark:border-slate-700">
                      {(group.custom_fields ?? []).filter((c) => c.id !== cf.id).map((c) => {
                        const sel = ((v("sync_field_ids") as string[] | null) ?? []).includes(c.id);
                        return (
                          <label key={c.id} className="flex items-center gap-2 text-sm">
                            <input type="checkbox" className="accent-indigo-600" checked={sel} onChange={(e) => {
                              const cur = (v("sync_field_ids") as string[] | null) ?? [];
                              set("sync_field_ids", e.target.checked ? [...cur, c.id] : cur.filter((x) => x !== c.id));
                            }} />
                            {c.label_name}
                          </label>
                        );
                      })}
                    </div>
                  </Row>
                </>
              )}

              {section === "screen" && (
                <>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <Check checked={Boolean(v("full_width"))} onChange={(b) => set("full_width", b)} label="Full width" />
                    <Check checked={Boolean(v("multiline"))} onChange={(b) => set("multiline", b)} label="Multi-line box" />
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Row label="Section heading" help="Blank = the default section for this field.">
                      <input className={INPUT} value={asStr(v("standalone_group_heading"))} onChange={(e) => set("standalone_group_heading", nullIfBlank(e.target.value))} />
                    </Row>
                    <Row label="Order within its section" help="Lowest first. Blank = default.">
                      <input className={INPUT} inputMode="numeric" value={asStr(v("field_order"))} onChange={(e) => set("field_order", e.target.value.trim() === "" ? null : Number(e.target.value.replace(/\D/g, "")))} />
                    </Row>
                  </div>
                  {kind === "cf" && (
                    <Row label="Shown on the tab of">
                      <select className={INPUT} value={asStr(v("display_document_id"))} onChange={(e) => set("display_document_id", nullIfBlank(e.target.value))}>
                        <option value="">Additional Details</option>
                        {group.documents.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                      </select>
                    </Row>
                  )}
                  {kind === "mark" && (
                    <Row label="Box colour">
                      <div className="flex gap-2">
                        {Object.entries(MARK_COLORS).map(([name, hex]) => (
                          <button key={name} type="button" title={name} onClick={() => set("color", name)}
                            className={`h-7 w-7 rounded-full border-2 ${v("color") === name ? "border-slate-900 dark:border-white" : "border-transparent"}`}
                            style={{ background: hex }} />
                        ))}
                      </div>
                    </Row>
                  )}
                </>
              )}

              {section === "history" && <HistorySection groupId={group.id} label={asStr(original.label_name)} />}
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
            {message && <span className="mr-auto text-sm text-slate-600 dark:text-slate-300">{message}</span>}
            {fixField && (
              <Button variant="secondary" onClick={() => setFixOpen(true)}>Fix prompt (AI suggestion)</Button>
            )}
            <Button variant="secondary" onClick={() => { setDraft({}); setVmode(parsePattern((original.validation_pattern as string) ?? null)); }} disabled={!dirty || saving}>
              Undo changes
            </Button>
            <Button onClick={save} isLoading={saving} disabled={!dirty}>Save to template</Button>
          </div>
        </div>
      )}
      {fixOpen && fixField && <FixPromptModal field={fixField} onClose={() => { setFixOpen(false); void reload(); }} />}
    </Modal>
  );
}

// ---- Reading & prompts (boxes) -----------------------------------------------------------------

function ReadingSection({ mark, v, set, onFix }: {
  mark: LiveMark; v: (k: string) => unknown; set: (k: string, val: unknown) => void; onFix: () => void;
}) {
  const [anchor, setAnchor] = useState("");
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState<string | null>(null);
  const [demo, setDemo] = useState<string | null>(null);
  const variations = (v("anchor_variations") as string[] | null) ?? [];

  async function check() {
    setChecking(true);
    setChecked(null);
    try {
      const out = await reviewsApi.formatCheck(mark.id, { value: asStr(v("example_value")) || null, format_prompt: asStr(v("tenant_format_prompt")) });
      setChecked(out);
    } catch (e) {
      setChecked(`(${apiError(e, "Check failed")})`);
    } finally {
      setChecking(false);
    }
  }

  async function test() {
    setDemo("Reading the sample…");
    try {
      const r = await onboardingApi.demoExtract(mark.document_id);
      const hit = r.results.find((x) => x.mark_id === mark.id);
      setDemo(hit ? (hit.extracted_values?.length ? hit.extracted_values.join(" | ") : hit.extracted_value ?? "(nothing read)") : "(not in the result)");
    } catch (e) {
      setDemo(apiError(e, "Could not read the sample."));
    }
  }

  return (
    <>
      <Row label="Label variations near the value" help={'Other spellings of the caption, e.g. "Inv No", "Invoice #".'}>
        <div className="flex flex-wrap gap-1.5 rounded-lg border border-slate-200 p-2 dark:border-slate-700">
          {variations.map((a) => (
            <span key={a} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-xs dark:bg-slate-800">
              {a}
              <button type="button" className="text-slate-400 hover:text-rose-600" onClick={() => set("anchor_variations", variations.filter((x) => x !== a))}>✕</button>
            </span>
          ))}
          <input
            className="min-w-[10rem] flex-1 bg-transparent px-1 text-sm outline-none"
            placeholder="Type and press Enter"
            value={anchor}
            onChange={(e) => setAnchor(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && anchor.trim()) { e.preventDefault(); set("anchor_variations", [...variations, anchor.trim()]); setAnchor(""); }
            }}
          />
        </div>
      </Row>
      <div className="grid gap-3 sm:grid-cols-2">
        <Row label="What it means">
          <input className={INPUT} value={asStr(v("semantic_description"))} onChange={(e) => set("semantic_description", nullIfBlank(e.target.value))} />
        </Row>
        <Row label="What it looks like (format hint)">
          <input className={INPUT} value={asStr(v("value_format_hint"))} onChange={(e) => set("value_format_hint", nullIfBlank(e.target.value))} />
        </Row>
      </div>
      <Row label="AI extraction prompt" help={<>The instruction the AI follows to read this field. <button type="button" className="font-semibold text-indigo-600 hover:underline" onClick={onFix}>Get an AI suggestion</button></>}>
        <textarea className={AREA} rows={6} value={asStr(v("extraction_prompt"))} onChange={(e) => set("extraction_prompt", nullIfBlank(e.target.value))} />
      </Row>
      <Row label="Format rule (Data Transformation)" help="Applied after reading, e.g. remove spaces and hyphens, company name only, 3 letters else blank.">
        <textarea className={AREA} rows={4} value={asStr(v("tenant_format_prompt"))} onChange={(e) => set("tenant_format_prompt", nullIfBlank(e.target.value))} />
      </Row>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" onClick={check} isLoading={checking} disabled={!asStr(v("tenant_format_prompt"))}>
          Check format rule on the example value
        </Button>
        {checked !== null && (
          <span className="text-xs">
            <span className="text-slate-500">{asStr(v("example_value")) || "(no example)"} → </span>
            <span className="font-mono font-semibold text-emerald-700 dark:text-emerald-300">{checked || "(blank)"}</span>
          </span>
        )}
        <Button size="sm" variant="secondary" onClick={test}>Test on the template sample</Button>
        {demo && <span className="font-mono text-xs text-slate-700 dark:text-slate-200">{demo}</span>}
      </div>
      <Row label="Correction prompt" help="Extra guidance when the reading keeps coming back wrong. Saved exactly as typed.">
        <textarea className={AREA} rows={3} value={asStr(v("correction_prompt"))} onChange={(e) => set("correction_prompt", nullIfBlank(e.target.value))} />
      </Row>
    </>
  );
}

// ---- Where the value comes from (custom fields) ----------------------------------------------

function KindSection({ group, cf, v, set, labels }: {
  group: LiveGroup; cf: LiveCustomField; v: (k: string) => unknown; set: (k: string, val: unknown) => void; labels: string[];
}) {
  const kind = asStr(v("kind")) || "hardcoded";
  const [columns, setColumns] = useState<string[] | null>(null);
  const [master, setMaster] = useState<string>("");

  useEffect(() => {
    if (kind !== "lookup") return;
    onboardingApi.getReferenceSheet(group.id)
      .then((s) => setMaster(s.attached ? `${s.file_name ?? "Material master"} · ${s.materials} keys${s.processing ? " · still processing" : ""}` : "No material master uploaded (Step 5 → Reference data)."))
      .catch(() => setMaster(""));
    onboardingApi.getReferenceColumns(group.id).then(setColumns).catch(() => setColumns([]));
  }, [kind, group.id]);

  const pieces = ((v("composite_source_labels") as CompositePiece[] | null) ?? []);
  const seps = ((v("composite_separators") as string[] | null) ?? []);
  const setPieces = (next: CompositePiece[], nextSeps = seps) => {
    set("composite_source_labels", next);
    set("composite_separators", next.slice(1).map((_, i) => nextSeps[i] ?? " "));
  };
  const sources = (v("source_document_ids") as string[] | null) ?? [];

  return (
    <>
      <Row label="Kind">
        <div className="flex flex-wrap gap-2">
          {[
            ["hardcoded", "Fixed value"],
            ["ai", "AI works it out"],
            ["lookup", "Look up in a sheet"],
            ["composite", "Join other fields"],
          ].map(([k, label]) => (
            <button key={k} type="button" onClick={() => set("kind", k)}
              className={`rounded-lg border px-3 py-1.5 text-sm ${kind === k ? "border-indigo-500 bg-indigo-50 font-semibold text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" : "border-slate-200 dark:border-slate-700"}`}>
              {label}
            </button>
          ))}
        </div>
      </Row>

      {kind === "hardcoded" && (
        <Row label="Value" help="The same on every job (the operator can still change it when asked).">
          <input className={INPUT} value={asStr(v("hardcoded_value"))} onChange={(e) => set("hardcoded_value", nullIfBlank(e.target.value))} />
        </Row>
      )}

      {kind === "ai" && (
        <>
          <Row label="AI instruction">
            <textarea className={AREA} rows={7} value={asStr(v("ai_prompt"))} onChange={(e) => set("ai_prompt", nullIfBlank(e.target.value))} />
          </Row>
          <Row label="Documents it reads" help="None ticked = every document.">
            <div className="flex flex-wrap gap-2">
              {group.documents.map((d) => (
                <label key={d.id} className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1 text-sm dark:border-slate-700">
                  <input type="checkbox" className="accent-indigo-600" checked={sources.includes(d.id)}
                    onChange={(e) => set("source_document_ids", e.target.checked ? [...sources, d.id] : sources.filter((x) => x !== d.id))} />
                  {d.name}
                </label>
              ))}
            </div>
          </Row>
        </>
      )}

      {kind === "lookup" && (
        <>
          {master && <p className={HELP}>{master}</p>}
          <Row label="Look up by (key fields, comma-separated)" help='e.g. "item_material_code" for CTH, or "Shipment Country,Shipment City".'>
            <input className={INPUT} value={asStr(v("lookup_key_label"))} onChange={(e) => set("lookup_key_label", nullIfBlank(e.target.value))} list={`labels-${cf.id}`} />
            <datalist id={`labels-${cf.id}`}>{labels.map((l) => <option key={l} value={l} />)}</datalist>
          </Row>
          <div className="grid gap-3 sm:grid-cols-2">
            <Row label="Match against column(s) of the sheet">
              <select multiple className={`${INPUT} h-28`} value={(v("lookup_match_columns") as string[] | null) ?? []}
                onChange={(e) => set("lookup_match_columns", [...e.target.selectedOptions].map((o) => o.value))}>
                {(columns ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </Row>
            <Row label="Bring back the column">
              <select className={INPUT} value={asStr(v("lookup_return_column"))} onChange={(e) => set("lookup_return_column", nullIfBlank(e.target.value))}>
                <option value="">— default —</option>
                {(columns ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </Row>
          </div>
          <Check checked={Boolean(v("lookup_ai_fallback"))} onChange={(b) => set("lookup_ai_fallback", b)} label="Ask the AI when the sheet has no match" />
          <Check checked={Boolean(v("skip_material_master"))} onChange={(b) => set("skip_material_master", b)} label="Skip the material master" help="Use only this field's own reference values." />
        </>
      )}

      {kind === "composite" && (
        <Row label="Pieces, in order" help="Each piece is another field or a fixed text; the separator sits between two pieces. Blank pieces are skipped.">
          <div className="flex flex-col gap-2">
            {pieces.map((p, i) => (
              <div key={i} className="flex flex-wrap items-center gap-2">
                {i > 0 && (
                  <input className={`${INPUT} w-20 font-mono`} title="Separator" value={seps[i - 1] ?? " "}
                    onChange={(e) => { const s = [...seps]; s[i - 1] = e.target.value; set("composite_separators", s); }} />
                )}
                {typeof p === "string" ? (
                  <select className={`${INPUT} flex-1`} value={p} onChange={(e) => { const n = [...pieces]; n[i] = e.target.value; setPieces(n); }}>
                    {labels.map((l) => <option key={l} value={l}>{l}</option>)}
                  </select>
                ) : (
                  <input className={`${INPUT} flex-1`} placeholder="Fixed text" value={p.fixed}
                    onChange={(e) => { const n = [...pieces]; n[i] = { fixed: e.target.value }; setPieces(n); }} />
                )}
                <button type="button" className="text-sm text-slate-400 hover:text-rose-600"
                  onClick={() => { const n = pieces.filter((_, j) => j !== i); const s = seps.filter((_, j) => j !== Math.max(0, i - 1)); setPieces(n, s); }}>✕</button>
              </div>
            ))}
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" onClick={() => setPieces([...pieces, labels[0] ?? ""])} disabled={!labels.length}>+ Field</Button>
              <Button size="sm" variant="secondary" onClick={() => setPieces([...pieces, { fixed: "" }])}>+ Fixed text</Button>
            </div>
          </div>
        </Row>
      )}
    </>
  );
}

// ---- Reference lookup ---------------------------------------------------------------------------

function ReferenceSection({ ownerType, ownerId, v, set }: {
  ownerType: onboardingApi.ReferenceOwnerType; ownerId: string; v: (k: string) => unknown; set: (k: string, val: unknown) => void;
}) {
  const [rows, setRows] = useState<onboardingApi.ReferenceValueRow[] | null>(null);
  const [filter, setFilter] = useState("");
  const [add, setAdd] = useState({ m1: "", m2: "", m3: "", m4: "", resolved: "" });
  const [note, setNote] = useState<string | null>(null);
  const on = Boolean(v("is_target_value"));

  async function load() {
    try { setRows(await onboardingApi.listReferenceValues(ownerType, ownerId)); } catch { setRows([]); }
  }
  useEffect(() => { if (on) void load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [on, ownerId]);

  async function addRow() {
    const match = [add.m1, add.m2, add.m3, add.m4].map((s) => s.trim());
    while (match.length && !match[match.length - 1]) match.pop();
    if (!match.length || !add.resolved.trim()) { setNote("Fill the match value(s) and the value it gives."); return; }
    try {
      const r = await onboardingApi.addReferenceValues(ownerType, ownerId, [{ match_values: match, resolved_value: add.resolved.trim() }]);
      setNote(`Added ${r.rows_added}, unchanged ${r.rows_unchanged}${r.conflicts?.length ? `, ${r.conflicts.length} conflict(s) - an existing row gives a different value` : ""}.`);
      setAdd({ m1: "", m2: "", m3: "", m4: "", resolved: "" });
      await load();
    } catch (e) { setNote(apiError(e, "Could not add the row.")); }
  }

  async function upload(file: File) {
    try {
      const r = await onboardingApi.uploadReferenceValues(ownerType, ownerId, file);
      setNote(`Upload: ${r.rows_added} added, ${r.rows_unchanged} unchanged, ${r.rows_skipped} skipped${r.conflicts?.length ? `, ${r.conflicts.length} conflict(s)` : ""}.`);
      await load();
    } catch (e) { setNote(apiError(e, "Upload failed.")); }
  }

  async function template() {
    try {
      const { url, filename } = await onboardingApi.downloadReferenceValuesTemplate(ownerType, ownerId);
      const a = document.createElement("a"); a.href = url; a.download = filename; a.click(); URL.revokeObjectURL(url);
    } catch (e) { setNote(apiError(e, "Could not download the template.")); }
  }

  const shown = (rows ?? []).filter((r) => !filter || JSON.stringify(r).toLowerCase().includes(filter.toLowerCase()));

  return (
    <>
      <Check checked={on} onChange={(b) => set("is_target_value", b)} label="Look the value up in its own reference table" help="A match replaces the value; no match leaves it empty. Save first, then manage the rows below." />
      {on && <Check checked={Boolean(v("fuzzy_match"))} onChange={(b) => set("fuzzy_match", b)} label="Fuzzy match" help={'Matches despite spacing / punctuation noise ("KUEHNE + NAGEL" = "KUEHNE+NAGEL").'} />}
      {on && rows && (
        <div className="flex flex-col gap-2 rounded-xl border border-slate-200 p-3 dark:border-slate-700">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold">Reference rows ({rows.length})</span>
            <input className={`${INPUT} ml-auto w-48`} placeholder="Search rows" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <Button size="sm" variant="secondary" onClick={template}>Download template</Button>
            <label className="cursor-pointer rounded-lg border border-slate-200 px-3 py-1.5 text-sm hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800">
              Upload sheet
              <input type="file" accept=".xlsx,.xls,.csv" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
            </label>
          </div>
          <div className="max-h-56 overflow-auto rounded-lg border border-slate-100 dark:border-slate-800">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-slate-50 text-left text-slate-500 dark:bg-slate-900">
                <tr><th className="px-2 py-1">Match 1</th><th className="px-2 py-1">Match 2</th><th className="px-2 py-1">Match 3</th><th className="px-2 py-1">Gives</th><th /></tr>
              </thead>
              <tbody>
                {shown.slice(0, 300).map((r) => (
                  <tr key={r.s_no} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="px-2 py-1">{r.match_value_1}</td><td className="px-2 py-1">{r.match_value_2}</td><td className="px-2 py-1">{r.match_value_3}</td>
                    <td className="px-2 py-1 font-mono font-semibold">{r.resolved_value}</td>
                    <td className="px-2 py-1 text-right">
                      <button type="button" className="text-slate-400 hover:text-rose-600" onClick={async () => {
                        if (!window.confirm(`Delete the row → ${r.resolved_value}?`)) return;
                        await onboardingApi.deleteReferenceValue(ownerType, ownerId, r.s_no); await load();
                      }}>✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {shown.length > 300 && <p className="p-2 text-[11px] text-slate-500">Showing 300 of {shown.length} - search to narrow.</p>}
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-6">
            {(["m1", "m2", "m3", "m4"] as const).map((k, i) => (
              <input key={k} className={INPUT} placeholder={`Match ${i + 1}`} value={add[k]} onChange={(e) => setAdd({ ...add, [k]: e.target.value })} />
            ))}
            <input className={INPUT} placeholder="Gives" value={add.resolved} onChange={(e) => setAdd({ ...add, resolved: e.target.value })} />
            <Button size="sm" onClick={addRow}>Add row</Button>
          </div>
          {note && <p className={HELP}>{note}</p>}
        </div>
      )}
    </>
  );
}

// ---- Cross-document check -------------------------------------------------------------------------

function CrossCheckSection({ group, mark, cf, v, set, onChanged }: {
  group: LiveGroup; mark?: LiveMark; cf?: LiveCustomField; v: (k: string) => unknown; set: (k: string, val: unknown) => void;
  onChanged: () => Promise<unknown>;
}) {
  const [pick, setPick] = useState<string[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const marksById = new Map(group.documents.flatMap((d) => d.marks.map((m) => [m.id, { m, d }] as const)));
  const links: CrossDocLink[] = group.cross_doc_links.filter((l) =>
    (mark && (l.source_mark_id === mark.id || l.target_mark_id === mark.id)) || (cf && l.source_custom_field_id === cf.id));

  async function remove(l: CrossDocLink) {
    if (!window.confirm("Remove this cross-check?")) return;
    try {
      if (cf && l.source_custom_field_id === cf.id) await onboardingApi.unlinkCustomFieldFromMark(cf.id, l.id);
      else await onboardingApi.deleteCrossDocLink(l.id);
      await onChanged();
    } catch (e) { setNote(apiError(e, "Could not remove it.")); }
  }

  async function addLinks() {
    if (!pick.length) return;
    try {
      if (mark) await onboardingApi.linkFieldToDocuments(mark.id, { target_document_ids: pick });
      else if (cf) await onboardingApi.linkCustomFieldToMarks(cf.id, { target_mark_ids: pick });
      setPick([]);
      setNote("Cross-check added.");
      await onChanged();
    } catch (e) { setNote(apiError(e, "Could not add the cross-check.")); }
  }

  return (
    <>
      <Check checked={Boolean(v("verify_with_other_document"))} onChange={(b) => set("verify_with_other_document", b)}
        label="This value also appears on another document" help="The job compares the two and flags a mismatch." />
      <div className="rounded-xl border border-slate-200 p-3 dark:border-slate-700">
        <p className="mb-2 text-sm font-semibold">Checks ({links.length})</p>
        {links.length === 0 && <p className={HELP}>None yet.</p>}
        <ul className="flex flex-col gap-1">
          {links.map((l) => {
            const src = l.source_mark_id ? marksById.get(l.source_mark_id) : null;
            const tgt = marksById.get(l.target_mark_id);
            return (
              <li key={l.id} className="flex items-center gap-2 text-sm">
                <span>{src ? `${src.m.label_name} (${src.d.name})` : cf?.label_name}</span>
                <span className="text-slate-400">must equal</span>
                <span>{tgt ? `${tgt.m.label_name} (${tgt.d.name})` : "?"}</span>
                <button type="button" className="ml-auto text-slate-400 hover:text-rose-600" onClick={() => remove(l)}>✕</button>
              </li>
            );
          })}
        </ul>
      </div>
      <Row label={mark ? "Check it on these documents too" : "Check it against these boxes"}>
        <div className="grid max-h-44 gap-1 overflow-y-auto rounded-lg border border-slate-200 p-2 dark:border-slate-700">
          {mark
            ? group.documents.filter((d) => d.id !== mark.document_id).map((d) => (
                <label key={d.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" className="accent-indigo-600" checked={pick.includes(d.id)}
                    onChange={(e) => setPick(e.target.checked ? [...pick, d.id] : pick.filter((x) => x !== d.id))} />
                  {d.name}
                </label>
              ))
            : group.documents.flatMap((d) => d.marks.filter((m) => m.width > 0).map((m) => (
                <label key={m.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" className="accent-indigo-600" checked={pick.includes(m.id)}
                    onChange={(e) => setPick(e.target.checked ? [...pick, m.id] : pick.filter((x) => x !== m.id))} />
                  {m.label_name} <span className="text-slate-400">· {d.name}</span>
                </label>
              )))}
        </div>
      </Row>
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={addLinks} disabled={!pick.length}>Add cross-check</Button>
        {note && <span className={HELP}>{note}</span>}
      </div>
    </>
  );
}

// ---- Prompt history -----------------------------------------------------------------------------

function HistorySection({ groupId, label }: { groupId: string; label: string }) {
  const [rows, setRows] = useState<PromptHistoryRow[] | null>(null);
  useEffect(() => {
    getPromptHistory(groupId).then((r) => setRows(r.filter((x) => x.label_name === label))).catch(() => setRows([]));
  }, [groupId, label]);
  if (!rows) return <p className={HELP}>Loading…</p>;
  if (!rows.length) return <p className={HELP}>No prompt changes recorded for this field yet.</p>;
  return (
    <ul className="flex max-h-[26rem] flex-col gap-2 overflow-y-auto">
      {rows.map((r) => (
        <li key={r.id} className="rounded-lg border border-slate-200 p-2 text-xs dark:border-slate-700">
          <p className="font-semibold">{r.action} · {r.changed_by ?? "—"} · {r.created_at ? new Date(r.created_at).toLocaleString() : ""}</p>
          {r.new_prompt !== r.old_prompt && (
            <p className="mt-1 whitespace-pre-wrap font-mono text-slate-600 dark:text-slate-300">{(r.new_prompt ?? "").slice(0, 500)}</p>
          )}
          {r.new_format_hint !== r.old_format_hint && <p className="mt-1 text-slate-500">Format hint: {r.new_format_hint ?? "—"}</p>}
        </li>
      ))}
    </ul>
  );
}
