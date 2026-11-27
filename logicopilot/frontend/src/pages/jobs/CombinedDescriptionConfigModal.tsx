import { useEffect, useState } from "react";
import axios from "axios";
import * as jobsApi from "../../api/jobs";
import { Modal } from "../../components/ui/Modal";
import { Button } from "../../components/ui/Button";
import { Input, Select } from "../../components/ui/Input";
import { Alert } from "../../components/ui/Alert";

// Combined Description Configuration - saved directly against a consignee (independent of any
// one job): the field order, each piece's own separator, and any fixed text, all reused
// automatically on that consignee's next job. See save_composite_field_config in
// app/api/v1/jobs.py for the backend half of this - this screen replaced the job screen's
// older inline "Combine fields" modal entirely (confirmed with the user).
//
// A job can carry SEVERAL sets: one format over lines 1-3, another over 4-6, and so on, since
// a shipment can hold products that genuinely need different descriptions.
//
// Set 1 is the one the consignee REMEMBERS. The others apply to the open job and nowhere
// else, and that is forced by what they are keyed on rather than chosen: a line range is
// positional, and positions do not survive to the next job, where lines 1-3 are different
// products entirely. "Rows 1-3 get this format" is simply not a statement that can be
// remembered about a consignee.

const SEPARATOR_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "(None)" },
  { value: " ", label: "Space" },
  { value: ",", label: "Comma (,)" },
  { value: ", ", label: "Comma + Space (, )" },
  { value: "-", label: "Hyphen (-)" },
  { value: " - ", label: "Space Hyphen Space ( - )" },
];

interface ConfigRow {
  kind: "field" | "fixed";
  fieldLabel: string;
  fixedText: string;
  separator: string;
}

/** One format, plus the stretch of the open job's product lines it is for. */
interface FormatSet {
  id: string;
  rows: ConfigRow[];
  /** Blank on Set 1 means "every product line". Required on any other set. */
  from: string;
  to: string;
}

function newId(): string {
  return Math.random().toString(36).slice(2);
}

function toRows(pieces: jobsApi.CompositePiece[], separators: string[]): ConfigRow[] {
  return pieces.map((p, i) => ({
    kind: typeof p === "string" ? "field" : "fixed",
    fieldLabel: typeof p === "string" ? p : "",
    fixedText: typeof p === "string" ? "" : p.fixed,
    separator: separators[i] ?? "",
  }));
}

function rowValue(row: ConfigRow, sampleRow: Record<string, string>): string {
  if (row.kind === "fixed") return row.fixedText;
  return sampleRow[row.fieldLabel] ?? "";
}

/** Joins rows for one sample product line, mirroring _join_composite_pieces in jobs.py exactly:
 *  each piece's own separator is inserted only once there is already real content to separate
 *  it from, and a piece with nothing for this line is skipped entirely, along with its own
 *  separator. */
function joinRows(rows: ConfigRow[], sampleRow: Record<string, string>): string {
  let out = "";
  for (const row of rows) {
    const value = rowValue(row, sampleRow);
    if (!value) continue;
    if (out) out += row.separator;
    out += value;
  }
  return out;
}

interface Props {
  open: boolean;
  onClose: () => void;
  groupId: string;
  /** Pre-selects this consignee on open (e.g. the open job's own consignee) - still changeable
   *  via the dropdown, since this screen can configure ANY consignee, not just one job's. */
  defaultConsignee?: string;
  /** When set, Save & Apply also recomputes THIS job immediately and hands back its updated
   *  field values - opened from a job screen rather than a standalone configuration entry. */
  jobId?: string;
  /** How many product lines the open job has. Without it there is nothing to range over, so
   *  the per-set line ranges and the "add another set" button are not offered at all. */
  productCount?: number;
  /** The name a source field is SHOWN under in the field dropdown (e.g. "Item Material Code"
   *  for item_material_code) - the job screen passes the same names its product cards use.
   *  Display only: the option value, and so what is saved, stays the field's label_name. */
  fieldName?: (label: string) => string;
  /** The boxes of the job's product card, in the card's order and under the card's names -
   *  when given, these are the fields offered (instead of every per-line field of the template). */
  cardFields?: { label: string; name: string }[];
  onSaved?: (fieldValues: jobsApi.CompositeFieldConfigSaveResult["field_values"]) => void;
}

export function CombinedDescriptionConfigModal({
  open, onClose, groupId, defaultConsignee, jobId, productCount, fieldName, cardFields, onSaved,
}: Props) {
  const cardName = new Map((cardFields ?? []).map((f) => [f.label, f.name]));
  const nameOf = (label: string) => cardName.get(label) ?? (fieldName ? fieldName(label) : label);
  const [consignees, setConsignees] = useState<string[]>([]);
  const [consignee, setConsignee] = useState("");
  const [labelName, setLabelName] = useState("Combined Description");
  const [sets, setSets] = useState<FormatSet[]>([]);
  const [availableLabels, setAvailableLabels] = useState<string[]>([]);
  const [sampleRows, setSampleRows] = useState<Record<string, string>[]>([]);
  const [loadingConsignees, setLoadingConsignees] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const canRange = !!jobId && (productCount ?? 0) > 0;

  useEffect(() => {
    if (!open) return;
    setConsignee(defaultConsignee ?? "");
    setSavedNote(null);
    setError(null);
    setLoadingConsignees(true);
    jobsApi.getKnownConsignees(groupId)
      .then(setConsignees)
      .catch(() => setConsignees([]))
      .finally(() => setLoadingConsignees(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, groupId]);

  useEffect(() => {
    if (!open || !consignee) {
      setSets([]);
      setAvailableLabels([]);
      setSampleRows([]);
      return;
    }
    setLoadingConfig(true);
    setError(null);
    jobsApi.getCompositeFieldConfig(groupId, consignee, labelName)
      .then((cfg) => {
        // Always back to ONE set on open. A range left over from last time would otherwise
        // silently skip most of the job on the next save.
        setSets([{ id: newId(), rows: toRows(cfg.pieces, cfg.separators), from: "", to: "" }]);
        setAvailableLabels(cfg.available_labels);
        setSampleRows(cfg.sample_rows);
      })
      .catch((err) => {
        setError(
          axios.isAxiosError(err)
            ? (err.response?.data?.detail as string | undefined) ?? "Could not load this consignee's format."
            : "Could not load this consignee's format.",
        );
      })
      .finally(() => setLoadingConfig(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, groupId, consignee]);

  const previewSample = sampleRows[0] ?? {};
  // What can go in: the product card's own boxes (same order, same names) when the job screen
  // sent them, else every per-line field of the template; never the combined field itself.
  const offered: { label: string; name: string }[] = (cardFields && cardFields.length
    ? cardFields
    : availableLabels.map((l) => ({ label: l, name: nameOf(l) }))).filter((f) => f.label !== labelName);
  const [view, setView] = useState<"wrap" | "row">("wrap");
  const [drag, setDrag] = useState<{ setId: string; from?: number; label?: string; fixed?: boolean } | null>(null);
  const [sepOpen, setSepOpen] = useState<string | null>(null);

  // ---- editing one set ---------------------------------------------------------------
  function patchSet(id: string, patch: Partial<FormatSet>) {
    setSets((arr) => arr.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  }
  function patchRow(setId: string, i: number, patch: Partial<ConfigRow>) {
    setSets((arr) => arr.map((s) => (s.id !== setId ? s : {
      ...s, rows: s.rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)),
    })));
  }
  function removeRow(setId: string, i: number) {
    setSets((arr) => arr.map((s) => (s.id !== setId ? s : {
      ...s, rows: s.rows.filter((_, idx) => idx !== i),
    })));
  }
  function addPiece(setId: string, piece: ConfigRow, at?: number) {
    setSets((arr) => arr.map((s) => {
      if (s.id !== setId) return s;
      const rows = [...s.rows];
      rows.splice(at ?? rows.length, 0, piece);
      return { ...s, rows };
    }));
  }
  function moveRowTo(setId: string, from: number, to: number) {
    setSets((arr) => arr.map((s) => {
      if (s.id !== setId || from === to) return s;
      const rows = [...s.rows];
      const [r] = rows.splice(from, 1);
      rows.splice(to > from ? to - 1 : to, 0, r);
      return { ...s, rows };
    }));
  }
  function duplicateSet(id: string) {
    setSets((arr) => {
      const src = arr.find((x) => x.id === id);
      if (!src) return arr;
      return [...arr, { id: newId(), rows: src.rows.map((r) => ({ ...r })), from: "", to: "" }];
    });
  }
  function addRow(setId: string) {
    setSets((arr) => arr.map((s) => {
      if (s.id !== setId) return s;
      const used = s.rows.filter((r) => r.kind === "field").map((r) => r.fieldLabel);
      const firstUnused = offered.map((f) => f.label).find((l) => !used.includes(l));
      return {
        ...s,
        rows: [...s.rows, firstUnused
          ? { kind: "field" as const, fieldLabel: firstUnused, fixedText: "", separator: "" }
          : { kind: "fixed" as const, fieldLabel: "", fixedText: "", separator: "" }],
      };
    }));
  }
  function moveRow(setId: string, i: number, dir: -1 | 1) {
    setSets((arr) => arr.map((s) => {
      if (s.id !== setId) return s;
      const next = [...s.rows];
      const j = i + dir;
      if (j < 0 || j >= next.length) return s;
      [next[i], next[j]] = [next[j], next[i]];
      return { ...s, rows: next };
    }));
  }

  // ---- adding and removing whole sets ------------------------------------------------
  function addSet() {
    setError(null);
    setSets((arr) => {
      // Start the new set where the last one left off, so "1-3, then 4-6" needs two numbers
      // typed instead of four, and cannot accidentally overlap.
      const last = arr[arr.length - 1];
      const lastTo = last ? Number(last.to) : NaN;
      const nextFrom = Number.isFinite(lastTo) && last?.to.trim() ? String(lastTo + 1) : "";
      // Start from a copy of set 1's rows rather than an empty table: a second format is
      // usually set 1 with a piece or two changed, and rebuilding it row by row is how pieces
      // get missed. Each row is copied, so editing the new set leaves set 1 untouched.
      const rows = (arr[0]?.rows ?? []).map((r) => ({ ...r }));
      return [...arr, { id: newId(), rows, from: nextFrom, to: "" }];
    });
  }
  function removeSet(id: string) {
    setError(null);
    setSets((arr) => arr.filter((s) => s.id !== id));
  }

  // ---- saving ------------------------------------------------------------------------
  /** Expand one set's from/to into line numbers, or report what is wrong with it. */
  function rangeOf(s: FormatSet, setNo: number, required: boolean): number[] | null | string {
    const blank = !s.from.trim() && !s.to.trim();
    if (blank) {
      if (required) return `Set ${setNo}: enter the first and last product number.`;
      return null;                       // Set 1 with no range = every product line
    }
    const from = Number(s.from);
    const to = Number(s.to);
    if (!s.from.trim() || !s.to.trim() || !Number.isFinite(from) || !Number.isFinite(to)) {
      return `Set ${setNo}: enter both the first and last product number.`;
    }
    if (from < 1 || to > (productCount ?? 0)) {
      return `Set ${setNo}: product numbers must be between 1 and ${productCount}.`;
    }
    if (from > to) return `Set ${setNo}: "from" (${from}) cannot be after "to" (${to}).`;
    const out: number[] = [];
    for (let i = from; i <= to; i++) out.push(i);
    return out;
  }

  async function handleSave() {
    if (!consignee.trim()) {
      setError("Pick a consignee first.");
      return;
    }
    if (sets.length === 0 || sets[0].rows.length === 0) {
      setError("Add at least one row to set 1.");
      return;
    }
    // Everything is checked BEFORE anything is sent: a job left half-reformatted, some lines
    // in the new shape and some in the old, is worse than a save that was refused.
    for (let n = 0; n < sets.length; n++) {
      const s = sets[n];
      if (s.rows.length === 0) {
        setError(`Set ${n + 1} has no rows in it.`);
        return;
      }
      for (const row of s.rows) {
        if (row.kind === "fixed" && !row.fixedText.trim()) {
          setError(`Set ${n + 1}: a fixed-text row cannot be empty.`);
          return;
        }
        if (row.kind === "field" && !row.fieldLabel) {
          setError(`Set ${n + 1}: choose a source field for every row, or remove it.`);
          return;
        }
      }
    }

    const ranges: (number[] | null)[] = [];
    if (canRange) {
      for (let n = 0; n < sets.length; n++) {
        const r = rangeOf(sets[n], n + 1, n > 0);
        if (typeof r === "string") {
          setError(r);
          return;
        }
        ranges.push(r);
      }
      // Two sets both claiming a line have no defined answer for it. Caught here as well as
      // server-side so the message names the sets while they are still on screen.
      const claimed = new Map<number, number>();
      for (let n = 0; n < ranges.length; n++) {
        for (const line of ranges[n] ?? []) {
          const owner = claimed.get(line);
          if (owner !== undefined && owner !== n) {
            setError(`Set ${owner + 1} and set ${n + 1} both cover product ${line}. `
              + "Give each set its own products.");
            return;
          }
          claimed.set(line, n);
        }
      }
    } else {
      sets.forEach(() => ranges.push(null));
    }

    const piecesOf = (s: FormatSet): jobsApi.CompositePiece[] =>
      s.rows.map((r) => (r.kind === "fixed" ? { fixed: r.fixedText.trim() } : r.fieldLabel));

    setSaving(true);
    setError(null);
    setSavedNote(null);
    try {
      const result = await jobsApi.saveCompositeFieldConfig(groupId, {
        consignee: consignee.trim(),
        labelName,
        pieces: piecesOf(sets[0]),
        separators: sets[0].rows.map((r) => r.separator),
        jobId,
        rowIndexes: ranges[0] ?? undefined,
        extraSets: sets.slice(1).map((s, i) => ({
          pieces: piecesOf(s),
          separators: s.rows.map((r) => r.separator),
          rowIndexes: ranges[i + 1] ?? [],
        })),
      });
      const applied = sets
        .map((s, i) => (ranges[i] ? `set ${i + 1} to products ${s.from}-${s.to}` : null))
        .filter(Boolean)
        .join(", ");
      setSavedNote(
        applied
          // Say WHICH lines moved. "Saved" alone, after choosing ranges, reads as if the whole
          // job was rewritten - the one thing the ranges exist to avoid.
          ? `Saved for ${consignee.trim()} — applied ${applied}.`
          : `Configuration saved for ${consignee.trim()}`,
      );
      onSaved?.(result.field_values);
      if (!consignees.includes(consignee.trim())) {
        setConsignees((arr) => [...arr, consignee.trim()].sort());
      }
    } catch (err) {
      setError(
        axios.isAxiosError(err)
          ? (err.response?.data?.detail as string | undefined) ?? "Could not save this format."
          : "Could not save this format.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function copyOutput(s: FormatSet) {
    try {
      await navigator.clipboard.writeText(joinRows(s.rows, previewSample));
      setCopiedId(s.id);
      setTimeout(() => setCopiedId(null), 1500);
    } catch {
      // Clipboard access can be blocked (permissions, insecure context) - nothing worth
      // surfacing as an error for a convenience button.
    }
  }

  const fieldTitle = cardName.get(labelName) ?? nameOf(labelName);
  const sepLabel = (v: string) => SEPARATOR_OPTIONS.find((o) => o.value === v)?.label ?? `"${v}"`;

  function dropAt(setId: string, at: number) {
    if (!drag || drag.setId !== setId) {
      if (drag && drag.from === undefined) {
        addPiece(setId, drag.fixed ? { kind: "fixed", fieldLabel: "", fixedText: "", separator: "" }
          : { kind: "field", fieldLabel: drag.label ?? "", fixedText: "", separator: "" }, at);
      }
      setDrag(null);
      return;
    }
    if (drag.from !== undefined) moveRowTo(setId, drag.from, at);
    else addPiece(setId, drag.fixed ? { kind: "fixed", fieldLabel: "", fixedText: "", separator: "" }
      : { kind: "field", fieldLabel: drag.label ?? "", fixedText: "", separator: "" }, at);
    setDrag(null);
  }

  const activeSetId = sets[sets.length - 1]?.id;

  return (
    <Modal open={open} onClose={onClose} title="Combined Description Configuration" maxWidth="max-w-6xl">
      <div className="flex flex-col gap-4">
        <p className="-mt-1 text-sm text-slate-500 dark:text-slate-400">
          Build <b className="text-slate-700 dark:text-slate-200">{fieldTitle}</b> by arranging the product card's fields,
          fixed text and separators. Set 1 is remembered for this consignee's future jobs; any further set is for this job only.
        </p>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-[1fr_18rem]">
          <Select label="Consignee" value={consignee} onChange={(e) => setConsignee(e.target.value)} disabled={loadingConsignees}>
            <option value="">{loadingConsignees ? "Loading…" : "Choose a consignee…"}</option>
            {consignees.map((c) => <option key={c} value={c}>{c}</option>)}
          </Select>
          <div>
            <Input label="Field (internal name)" value={labelName} onChange={(e) => setLabelName(e.target.value)} placeholder="Combined Description" />
            {fieldTitle !== labelName && <p className="mt-1 text-xs text-slate-500">Shown on the product card as “{fieldTitle}”</p>}
          </div>
        </div>

        {savedNote && (
          <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">
            <span>✅</span> {savedNote}
          </div>
        )}
        {error && <Alert>{error}</Alert>}

        {!consignee ? (
          <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-400 dark:border-slate-700">
            Pick a consignee above to configure their Combined Description format.
          </p>
        ) : loadingConfig ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[15rem_1fr]">
            {/* Palette */}
            <aside className="h-fit rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/40 lg:sticky lg:top-0">
              <p className="text-sm font-semibold text-slate-900 dark:text-slate-50">Add to description</p>
              <p className="mb-2 text-xs text-slate-500">Click to add to the last set, or drag into place.</p>
              <button type="button" draggable
                onDragStart={() => setDrag({ setId: "", fixed: true })} onDragEnd={() => setDrag(null)}
                onClick={() => activeSetId && addPiece(activeSetId, { kind: "fixed", fieldLabel: "", fixedText: "", separator: "" })}
                className="mb-2 flex w-full items-center gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-left text-sm hover:border-indigo-300 hover:bg-indigo-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-indigo-500/10">
                <span className="flex h-7 w-7 items-center justify-center rounded-md bg-violet-100 font-serif font-bold text-violet-700 dark:bg-violet-500/20 dark:text-violet-300">T</span>
                <span><b className="block text-slate-900 dark:text-slate-50">Fixed text</b><span className="text-xs text-slate-500">e.g. ( ) / -</span></span>
              </button>
              <p className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Fields on the product card</p>
              <div className="flex max-h-80 flex-col gap-1 overflow-y-auto pr-1">
                {offered.map((f) => {
                  const usedAt = (sets.find((x) => x.id === activeSetId)?.rows ?? []).findIndex((r) => r.kind === "field" && r.fieldLabel === f.label);
                  return (
                    <button key={f.label} type="button" draggable
                      onDragStart={() => setDrag({ setId: "", label: f.label })} onDragEnd={() => setDrag(null)}
                      onClick={() => activeSetId && addPiece(activeSetId, { kind: "field", fieldLabel: f.label, fixedText: "", separator: "" })}
                      className="flex items-center justify-between gap-2 rounded-lg border border-transparent bg-white px-2.5 py-1.5 text-left text-sm hover:border-indigo-300 hover:bg-indigo-50 dark:bg-slate-900 dark:hover:bg-indigo-500/10">
                      <span className="flex items-center gap-2 text-slate-800 dark:text-slate-100">
                        <span className="text-slate-400">⋮⋮</span>{f.name}
                      </span>
                      {usedAt >= 0 && <span className="shrink-0 rounded-full bg-emerald-100 px-1.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300">#{usedAt + 1}</span>}
                    </button>
                  );
                })}
                {offered.length === 0 && <p className="text-xs text-slate-500">No product fields found on this template.</p>}
              </div>
              <p className="mt-3 rounded-lg bg-white p-2 text-[11px] text-slate-500 dark:bg-slate-900">
                <b>Separators</b> sit between the cards - click a chip to choose one. A separator only prints when there is text on both sides.
              </p>
            </aside>

            <div className="flex flex-col gap-4">
              {sets.map((s, setIdx) => {
                const liveOutput = joinRows(s.rows, previewSample);
                const isFirst = setIdx === 0;
                return (
                  <section key={s.id} className="rounded-xl border border-slate-200 p-4 dark:border-slate-700">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-indigo-100 text-sm font-bold text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300">{setIdx + 1}</span>
                        <span className="font-semibold text-slate-800 dark:text-slate-100">Set {setIdx + 1}</span>
                        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${isFirst ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300" : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"}`}>
                          {isFirst ? "Used for future jobs" : "This job only"}
                        </span>
                        <span className="text-xs text-slate-500">{s.rows.length} piece{s.rows.length === 1 ? "" : "s"}</span>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <div className="inline-flex rounded-lg border border-slate-200 p-0.5 dark:border-slate-700">
                          {([["wrap", "Wrap view"], ["row", "Single row"]] as const).map(([k, l]) => (
                            <button key={k} type="button" onClick={() => setView(k)}
                              className={`rounded-md px-2.5 py-1 text-xs font-medium ${view === k ? "bg-indigo-600 text-white" : "text-slate-600 dark:text-slate-300"}`}>{l}</button>
                          ))}
                        </div>
                        <Button variant="secondary" size="sm" onClick={() => addRow(s.id)}>+ Add piece</Button>
                        {canRange && (
                          <button type="button" title="Duplicate this set" onClick={() => duplicateSet(s.id)}
                            className="rounded-md border border-slate-200 px-2 py-1 text-xs hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800">⧉ Duplicate</button>
                        )}
                        {!isFirst && (
                          <button type="button" onClick={() => removeSet(s.id)}
                            className="rounded-md border border-red-200 px-2 py-1 text-xs text-red-600 hover:bg-red-50 dark:border-red-500/30 dark:hover:bg-red-500/10">Delete set</button>
                        )}
                      </div>
                    </div>

                    <p className="mb-2 text-xs text-slate-500">Drag the cards to reorder. Click a field name to change it.</p>
                    <div className={`flex items-stretch gap-1 rounded-lg bg-slate-50 p-2 dark:bg-slate-800/40 ${view === "wrap" ? "flex-wrap" : "overflow-x-auto"}`}
                      onDragOver={(e) => e.preventDefault()} onDrop={() => dropAt(s.id, s.rows.length)}>
                      {s.rows.length === 0 && (
                        <p className="w-full p-4 text-center text-xs text-slate-400">Drop or click a field on the left to start.</p>
                      )}
                      {s.rows.map((row, i) => {
                        const value = rowValue(row, previewSample);
                        const key = `${s.id}:${i}`;
                        return (
                          <div key={key} className="flex items-stretch">
                            {i > 0 && (
                              <div className="relative flex items-center px-0.5">
                                <button type="button" onClick={() => setSepOpen(sepOpen === key ? null : key)}
                                  title="Separator before this piece"
                                  className={`whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-semibold ${row.separator ? "border-indigo-300 bg-indigo-50 text-indigo-700 dark:border-indigo-500/40 dark:bg-indigo-500/15 dark:text-indigo-300" : "border-dashed border-slate-300 bg-white text-slate-400 dark:border-slate-600 dark:bg-slate-900"}`}>
                                  {row.separator ? (row.separator.trim() === "" ? "␣" : `“${row.separator}”`) : "+"}
                                </button>
                                {sepOpen === key && (
                                  <div className="absolute left-0 top-8 z-20 w-56 rounded-lg border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-900">
                                    {SEPARATOR_OPTIONS.map((opt) => (
                                      <button key={opt.label} type="button"
                                        onClick={() => { patchRow(s.id, i, { separator: opt.value }); setSepOpen(null); }}
                                        className={`block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-indigo-50 dark:hover:bg-indigo-500/10 ${row.separator === opt.value ? "font-semibold text-indigo-700 dark:text-indigo-300" : "text-slate-700 dark:text-slate-200"}`}>
                                        {row.separator === opt.value ? "✓ " : ""}{opt.label}
                                      </button>
                                    ))}
                                    <button type="button" className="block w-full rounded px-2 py-1.5 text-left text-sm text-slate-700 hover:bg-indigo-50 dark:text-slate-200"
                                      onClick={() => { const v = window.prompt("Separator text (e.g. \" / \")", row.separator); if (v !== null) patchRow(s.id, i, { separator: v }); setSepOpen(null); }}>
                                      Custom… {SEPARATOR_OPTIONS.some((o) => o.value === row.separator) ? "" : `(${sepLabel(row.separator)})`}
                                    </button>
                                  </div>
                                )}
                              </div>
                            )}
                            <div draggable onDragStart={() => setDrag({ setId: s.id, from: i })} onDragEnd={() => setDrag(null)}
                              onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.stopPropagation(); dropAt(s.id, i); }}
                              className={`flex w-44 cursor-grab flex-col gap-1.5 rounded-xl border bg-white p-2.5 shadow-sm active:cursor-grabbing dark:bg-slate-900 ${drag?.setId === s.id && drag.from === i ? "opacity-40" : ""} ${row.kind === "fixed" ? "border-violet-200 dark:border-violet-500/30" : "border-slate-200 dark:border-slate-700"}`}>
                              <div className="flex items-center justify-between">
                                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-indigo-600 text-[10px] font-bold text-white">{i + 1}</span>
                                <span className="flex items-center gap-0.5">
                                  <button type="button" title="Move left" disabled={i === 0} onClick={() => moveRow(s.id, i, -1)}
                                    className="rounded px-0.5 text-[10px] text-slate-400 hover:text-indigo-600 disabled:opacity-30">◀</button>
                                  <button type="button" title="Move right" disabled={i === s.rows.length - 1} onClick={() => moveRow(s.id, i, 1)}
                                    className="rounded px-0.5 text-[10px] text-slate-400 hover:text-indigo-600 disabled:opacity-30">▶</button>
                                  <span className="text-slate-300" title="Drag to move">⋮⋮</span>
                                  <button type="button" title="Delete" onClick={() => removeRow(s.id, i)} className="rounded p-0.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600">✕</button>
                                </span>
                              </div>
                              {row.kind === "fixed" ? (
                                <>
                                  <span className="text-xs font-semibold text-violet-700 dark:text-violet-300">Fixed text</span>
                                  <input value={row.fixedText} onChange={(e) => patchRow(s.id, i, { fixedText: e.target.value })} placeholder="e.g. ("
                                    className="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-center text-sm dark:border-slate-700 dark:bg-slate-900" />
                                </>
                              ) : (
                                <>
                                  <select value={row.fieldLabel} onChange={(e) => patchRow(s.id, i, { fieldLabel: e.target.value })}
                                    className="w-full truncate rounded-md border border-transparent bg-transparent px-1 py-0.5 text-xs font-semibold text-slate-800 hover:border-slate-200 dark:text-slate-100">
                                    {row.fieldLabel && !offered.some((f) => f.label === row.fieldLabel) && <option value={row.fieldLabel}>{nameOf(row.fieldLabel)}</option>}
                                    {offered.map((f) => <option key={f.label} value={f.label}>{f.name}</option>)}
                                  </select>
                                  <span className={`truncate rounded-md px-2 py-1 text-center text-sm font-medium ${value ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-200" : "bg-slate-100 text-slate-400 dark:bg-slate-800"}`} title={value}>
                                    {value || "empty on sample"}
                                  </span>
                                </>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    <div className="mt-3 flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 dark:border-emerald-500/20 dark:bg-emerald-500/10">
                      <span className="text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-400">Live output</span>
                      <span className="flex-1 break-all font-medium text-emerald-900 dark:text-emerald-100">{liveOutput || "—"}</span>
                      <button type="button" onClick={() => copyOutput(s)}
                        className="rounded-md border border-emerald-300 bg-white px-2 py-1 text-xs text-emerald-700 hover:bg-emerald-50 dark:border-emerald-500/30 dark:bg-slate-900 dark:text-emerald-300">
                        {copiedId === s.id ? "Copied!" : "Copy"}
                      </button>
                    </div>

                    {canRange && (
                      <div className="mt-3 flex flex-wrap items-end gap-3">
                        <Input label="From product" type="number" min={1} max={productCount} value={s.from}
                          onChange={(e) => patchSet(s.id, { from: e.target.value })} className="w-28" />
                        <Input label="To product" type="number" min={1} max={productCount} value={s.to}
                          onChange={(e) => patchSet(s.id, { to: e.target.value })} className="w-28" />
                        <p className="pb-2 text-xs text-slate-500 dark:text-slate-400">
                          {isFirst ? `of ${productCount} on this job — leave both blank to apply set 1 to every product` : `of ${productCount} on this job`}
                        </p>
                      </div>
                    )}
                  </section>
                );
              })}

              {canRange && (
                <div>
                  <Button variant="secondary" size="sm" onClick={addSet}>+ Add another set</Button>
                  <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
                    A second set lets different products carry a different description — set 1 for products 1-3, set 2 for 4-6, and so on.
                    A new set starts as a copy of set 1. Only set 1 is remembered for this consignee's future jobs.
                  </p>
                </div>
              )}
            </div>
          </div>
        )}

        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={handleSave} isLoading={saving} disabled={loadingConfig || !consignee}>✓ Save &amp; Apply</Button>
        </div>
      </div>
    </Modal>
  );
}
