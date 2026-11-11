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
  onSaved?: (fieldValues: jobsApi.CompositeFieldConfigSaveResult["field_values"]) => void;
}

export function CombinedDescriptionConfigModal({
  open, onClose, groupId, defaultConsignee, jobId, productCount, onSaved,
}: Props) {
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
  function addRow(setId: string) {
    setSets((arr) => arr.map((s) => {
      if (s.id !== setId) return s;
      const used = s.rows.filter((r) => r.kind === "field").map((r) => r.fieldLabel);
      const firstUnused = availableLabels.find((l) => !used.includes(l));
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
      return [...arr, { id: newId(), rows: [], from: nextFrom, to: "" }];
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

  return (
    <Modal open={open} onClose={onClose} title="Combined Description Configuration" maxWidth="max-w-4xl">
      <div className="flex flex-col gap-5">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Set the format for combining fields. Set 1 is saved against the consignee and used for
          all their future jobs. Any further set applies to this job only, over the product
          lines you give it.
        </p>

        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[280px] flex-1">
            <Select
              label="Consignee"
              value={consignee}
              onChange={(e) => setConsignee(e.target.value)}
              disabled={loadingConsignees}
            >
              <option value="">{loadingConsignees ? "Loading…" : "Choose a consignee…"}</option>
              {consignees.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </Select>
          </div>
          <div className="min-w-[200px]">
            <Input
              label="Field label"
              value={labelName}
              onChange={(e) => setLabelName(e.target.value)}
              placeholder="Combined Description"
            />
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
          <>
            {sets.map((s, setIdx) => {
              const used = s.rows.filter((r) => r.kind === "field").map((r) => r.fieldLabel);
              const liveOutput = joinRows(s.rows, previewSample);
              const isFirst = setIdx === 0;
              return (
                <div
                  key={s.id}
                  className="rounded-xl border border-slate-200 p-4 dark:border-slate-700"
                >
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-indigo-100 text-xs font-bold text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300">
                        {setIdx + 1}
                      </span>
                      <span className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                        Set {setIdx + 1}
                      </span>
                      <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                        isFirst
                          ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
                          : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
                      }`}>
                        {isFirst ? "used for future jobs" : "this job only"}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Button variant="secondary" size="sm" onClick={() => addRow(s.id)}>
                        + Add Row
                      </Button>
                      {!isFirst && (
                        <button
                          type="button"
                          onClick={() => removeSet(s.id)}
                          className="rounded-md border border-red-200 px-2 py-1 text-xs text-red-600 hover:bg-red-50 dark:border-red-500/30 dark:hover:bg-red-500/10"
                        >
                          Remove set
                        </button>
                      )}
                    </div>
                  </div>

                  {s.rows.length === 0 ? (
                    <p className="rounded-lg border border-dashed border-slate-300 p-4 text-center text-xs text-slate-400 dark:border-slate-700">
                      No rows yet - add one to start building this format.
                    </p>
                  ) : (
                    <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
                      <table className="w-full text-sm">
                        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-800/50 dark:text-slate-400">
                          <tr>
                            <th className="px-3 py-2 text-left">Order</th>
                            <th className="w-56 px-3 py-2 text-left">Source Field</th>
                            <th className="w-48 px-3 py-2 text-left">Separator (before)</th>
                            <th className="px-3 py-2 text-left">Preview Piece</th>
                            <th className="px-3 py-2 text-left">Actions</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                          {s.rows.map((row, i) => {
                            const value = rowValue(row, previewSample);
                            return (
                              <tr key={i}>
                                <td className="px-3 py-2">
                                  <div className="flex items-center gap-1">
                                    <span className="flex h-6 w-6 items-center justify-center rounded bg-slate-100 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                                      {i + 1}
                                    </span>
                                    <button type="button" disabled={i === 0} onClick={() => moveRow(s.id, i, -1)}
                                      className="rounded px-1 text-slate-400 hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-slate-800" title="Move up">▲</button>
                                    <button type="button" disabled={i === s.rows.length - 1} onClick={() => moveRow(s.id, i, 1)}
                                      className="rounded px-1 text-slate-400 hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-slate-800" title="Move down">▼</button>
                                  </div>
                                </td>
                                <td className="px-3 py-2">
                                  <div className="flex flex-col gap-1.5">
                                    <select
                                      value={row.kind === "fixed" ? "__fixed__" : row.fieldLabel}
                                      onChange={(e) => {
                                        const v = e.target.value;
                                        if (v === "__fixed__") patchRow(s.id, i, { kind: "fixed", fieldLabel: "" });
                                        else patchRow(s.id, i, { kind: "field", fieldLabel: v, fixedText: "" });
                                      }}
                                      className="w-full min-w-[9rem] rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
                                    >
                                      <option value="__fixed__">Fixed Text</option>
                                      {row.kind === "field" && row.fieldLabel && !availableLabels.includes(row.fieldLabel) && (
                                        <option value={row.fieldLabel}>{row.fieldLabel}</option>
                                      )}
                                      {availableLabels
                                        .filter((l) => l === row.fieldLabel || !used.includes(l))
                                        .map((l) => (
                                          <option key={l} value={l}>{l}</option>
                                        ))}
                                    </select>
                                    {row.kind === "fixed" && (
                                      <input
                                        type="text"
                                        value={row.fixedText}
                                        onChange={(e) => patchRow(s.id, i, { fixedText: e.target.value })}
                                        placeholder="Type the fixed text, e.g. ("
                                        className="w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
                                      />
                                    )}
                                  </div>
                                </td>
                                <td className="px-3 py-2">
                                  <select
                                    value={row.separator}
                                    onChange={(e) => patchRow(s.id, i, { separator: e.target.value })}
                                    className="w-full min-w-[8rem] rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
                                  >
                                    {SEPARATOR_OPTIONS.map((opt) => (
                                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                                    ))}
                                  </select>
                                </td>
                                <td className="px-3 py-2">
                                  <span className="rounded bg-emerald-50 px-2 py-1 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
                                    {value || "—"}
                                  </span>
                                </td>
                                <td className="px-3 py-2">
                                  <button type="button" onClick={() => removeRow(s.id, i)}
                                    className="rounded p-1 text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10" title="Delete row">
                                    🗑
                                  </button>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}

                  <div className="mt-3">
                    <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-400">
                      Live output for set {setIdx + 1}
                    </p>
                    <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 dark:border-emerald-500/20 dark:bg-emerald-500/10">
                      <span className="flex-1 font-medium text-emerald-900 dark:text-emerald-100">
                        {liveOutput || "—"}
                      </span>
                      <button type="button" onClick={() => copyOutput(s)}
                        className="rounded-md border border-emerald-300 bg-white px-2 py-1 text-xs text-emerald-700 hover:bg-emerald-50 dark:border-emerald-500/30 dark:bg-slate-900 dark:text-emerald-300">
                        {copiedId === s.id ? "Copied!" : "Copy"}
                      </button>
                    </div>
                  </div>

                  {canRange && (
                    <div className="mt-3 flex flex-wrap items-end gap-3 border-t border-slate-100 pt-3 dark:border-slate-800">
                      <Input
                        label="From product"
                        type="number"
                        min={1}
                        max={productCount}
                        value={s.from}
                        onChange={(e) => patchSet(s.id, { from: e.target.value })}
                        className="w-32"
                      />
                      <Input
                        label="To product"
                        type="number"
                        min={1}
                        max={productCount}
                        value={s.to}
                        onChange={(e) => patchSet(s.id, { to: e.target.value })}
                        className="w-32"
                      />
                      <p className="pb-2 text-xs text-slate-500 dark:text-slate-400">
                        {isFirst
                          ? `of ${productCount} on this job — leave both blank to apply set 1 to every product`
                          : `of ${productCount} on this job`}
                      </p>
                    </div>
                  )}
                </div>
              );
            })}

            {canRange && (
              <div>
                <Button variant="secondary" size="sm" onClick={addSet}>
                  + Add another set
                </Button>
                <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
                  A second set lets different products carry a different description — set 1 for
                  products 1-3, set 2 for 4-6, and so on. Sets cannot cover the same product
                  twice. Only set 1 is remembered for this consignee's future jobs, because a
                  product's POSITION means something different on every job.
                </p>
              </div>
            )}
          </>
        )}

        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={handleSave} isLoading={saving} disabled={loadingConfig || !consignee}>
            Save &amp; Apply
          </Button>
        </div>
      </div>
    </Modal>
  );
}
