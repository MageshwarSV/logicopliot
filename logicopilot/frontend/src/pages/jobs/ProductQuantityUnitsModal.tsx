import { useMemo, useState } from "react";
import axios from "axios";
import * as jobsApi from "../../api/jobs";
import type { JobFieldValue } from "../../types/jobs";
import { Modal } from "../../components/ui/Modal";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Alert } from "../../components/ui/Alert";

// Bulk-set a per-row "unit" field (PCS/BOX/KG/...) across some or all of a document's product
// lines in one go, instead of typing it 36 times. Reuses correctFieldValue - the exact same
// PATCH every other per-row edit in this screen already goes through - rather than a new bulk
// endpoint, so there is nothing new for the backend to learn about a "bulk" operation at all.
//
// Which field is "the unit" is found by label pattern, not a hardcoded exact string - label
// wording is set per tenant/template (this one happens to call it "product quantity type";
// another customer's could read "UOM" or "Unit of Measure"), and the same patterns already
// exist in jobs.py's own _row_read_looks_broken for exactly this reason.
const UNIT_FIELD_PATTERN = /quantity.?type|unit.?of.?measure|\buom\b|quantity.?unit/i;

function findUnitField(cells: JobFieldValue[]): JobFieldValue | undefined {
  return cells.find((fv) => UNIT_FIELD_PATTERN.test(fv.label_name));
}

interface RangeRow {
  id: string;
  from: string;
  to: string;
  unit: string;
}

function newRangeRow(): RangeRow {
  return { id: Math.random().toString(36).slice(2), from: "", to: "", unit: "" };
}

interface Props {
  open: boolean;
  onClose: () => void;
  /** This document's own per-row field values, grouped by row - same shape ExtractionReview
   *  already builds for its product cards (lineRows). */
  lineRows: [number, JobFieldValue[]][];
  /** Parent's own full-job refetch, called once after a successful apply. */
  onApplied: () => Promise<void>;
}

export function ProductQuantityUnitsModal({ open, onClose, lineRows, onApplied }: Props) {
  const [mode, setMode] = useState<"choice" | "all" | "selected">("choice");
  const [allValue, setAllValue] = useState("");
  const [ranges, setRanges] = useState<RangeRow[]>([newRangeRow()]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const unitFieldLabel = useMemo(() => {
    for (const [, cells] of lineRows) {
      const fv = findUnitField(cells);
      if (fv) return fv.label_name;
    }
    return null;
  }, [lineRows]);
  // How many products are actually on this document - shown so the operator knows the total
  // before picking a range (e.g. whether "1-10, 11-20" already covers everything, or there is
  // a 21st product still left out).
  const totalProducts = lineRows.length;

  function reset() {
    setMode("choice");
    setAllValue("");
    setRanges([newRangeRow()]);
    setError(null);
    setSuccessMsg(null);
  }
  function handleClose() {
    if (saving) return;
    reset();
    onClose();
  }

  function updateRange(id: string, patch: Partial<RangeRow>) {
    setRanges((arr) => arr.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }
  function removeRange(id: string) {
    setRanges((arr) => (arr.length > 1 ? arr.filter((r) => r.id !== id) : arr));
  }
  function addRange() {
    setRanges((arr) => {
      // Picks up right where the LAST row's own To Count left off - e.g. a row already
      // reading 1-10 gets a fresh row starting at 11, instead of an empty box the operator
      // has to work out and retype by hand every time. Only when that last row actually has
      // a valid To Count of its own; a wholly blank or non-numeric last row leaves the new
      // row's From Count blank too, same as before.
      const last = arr[arr.length - 1];
      const lastTo = last ? Number(last.to) : NaN;
      const nextFrom = last && last.to.trim() !== "" && Number.isFinite(lastTo) ? String(lastTo + 1) : "";
      return [...arr, { ...newRangeRow(), from: nextFrom }];
    });
  }

  async function runApply(updates: { id: string; value: string }[], summary: string) {
    setSaving(true);
    setError(null);
    setSuccessMsg(null);
    try {
      for (const u of updates) {
        await jobsApi.correctFieldValue(u.id, u.value);
      }
      await onApplied();
      setSuccessMsg(summary);
    } catch (err) {
      setError(
        axios.isAxiosError(err)
          ? (err.response?.data?.detail as string | undefined) ?? "Could not apply the unit to every product."
          : "Could not apply the unit to every product.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function applyAll() {
    const value = allValue.trim();
    if (!value) {
      setError("Enter a unit first.");
      return;
    }
    const updates = lineRows
      .map(([, cells]) => findUnitField(cells))
      .filter((fv): fv is JobFieldValue => !!fv)
      .map((fv) => ({ id: fv.id, value }));
    if (!updates.length) {
      setError("No product rows have a quantity-unit field on this document.");
      return;
    }
    await runApply(updates, `Applied "${value}" to ${updates.length} product${updates.length === 1 ? "" : "s"}.`);
  }

  function validateRanges(): { from: number; to: number; unit: string }[] | null {
    const parsed: { from: number; to: number; unit: string }[] = [];
    for (let i = 0; i < ranges.length; i++) {
      const r = ranges[i];
      if (!r.from.trim() && !r.to.trim() && !r.unit.trim()) continue; // a wholly untouched extra row is just ignored
      if (!r.from.trim() || !r.to.trim() || !r.unit.trim()) {
        setError(`Row ${i + 1}: fill in From Count, To Count and Unit, or remove the row.`);
        return null;
      }
      const from = Number(r.from);
      const to = Number(r.to);
      if (!Number.isFinite(from) || !Number.isFinite(to)) {
        setError(`Row ${i + 1}: From Count and To Count must be numbers.`);
        return null;
      }
      if (from > to) {
        setError(`Row ${i + 1}: From Count must not be greater than To Count.`);
        return null;
      }
      parsed.push({ from, to, unit: r.unit.trim() });
    }
    if (!parsed.length) {
      setError("Add at least one range.");
      return null;
    }
    const sorted = [...parsed].sort((a, b) => a.from - b.from);
    for (let i = 1; i < sorted.length; i++) {
      // Both ends inclusive (1-10 means products 1 through 10, 10 products) - so the next
      // range has to start strictly after this one ends, e.g. 11-20 right after 1-10. A
      // touching boundary (1-10 then 10-20) is treated as a real overlap, not allowed - "10"
      // would otherwise match two rows at once with no way to say which one wins.
      if (sorted[i].from <= sorted[i - 1].to) {
        setError(
          `Ranges overlap: ${sorted[i - 1].from}-${sorted[i - 1].to} and ${sorted[i].from}-${sorted[i].to}.`,
        );
        return null;
      }
    }
    return parsed;
  }

  async function applySelected() {
    const parsed = validateRanges();
    if (!parsed) return;
    const updates: { id: string; value: string }[] = [];
    // "Count" is the product's own position - the same number shown as "Product N" on its
    // card (FieldMark.row_index) - not its Item Quantity value. Confirmed live: a 144-line
    // invoice with real quantities in the thousands matched nothing when this used to read
    // the quantity value instead.
    for (const [rowIndex, cells] of lineRows) {
      const unitFv = findUnitField(cells);
      if (!unitFv) continue;
      const match = parsed.find((r) => rowIndex >= r.from && rowIndex <= r.to);
      if (!match) continue;
      updates.push({ id: unitFv.id, value: match.unit });
    }
    if (!updates.length) {
      setError("No product's position falls inside any of these ranges - nothing to apply.");
      return;
    }
    await runApply(
      updates,
      `Applied a unit to ${updates.length} product${updates.length === 1 ? "" : "s"} matching a range.`,
    );
  }

  return (
    <Modal open={open} onClose={handleClose} title="Apply All Product Units" maxWidth="max-w-2xl">
      <div className="flex flex-col gap-5">
        {unitFieldLabel && (
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Sets <span className="font-medium text-slate-700 dark:text-slate-200">{unitFieldLabel}</span> across
            this document's product lines.
          </p>
        )}
        <p className="text-xs font-medium text-slate-500 dark:text-slate-400">
          Job Product Detail · {totalProducts} product{totalProducts === 1 ? "" : "s"}
        </p>
        {error && <Alert>{error}</Alert>}
        {successMsg && (
          <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">
            <span>✅</span> {successMsg}
          </div>
        )}

        {mode === "choice" && (
          <div className="grid gap-3 sm:grid-cols-2">
            <button
              type="button"
              onClick={() => { setError(null); setSuccessMsg(null); setMode("all"); }}
              className="flex flex-col items-start gap-1 rounded-lg border border-slate-200 p-4 text-left hover:border-teal-300 hover:bg-teal-50 dark:border-slate-700 dark:hover:border-teal-500/40 dark:hover:bg-teal-500/10"
            >
              <span className="font-medium text-slate-800 dark:text-slate-100">Apply for All Products</span>
              <span className="text-xs text-slate-500 dark:text-slate-400">
                One unit, set on every product line at once.
              </span>
            </button>
            <button
              type="button"
              onClick={() => { setError(null); setSuccessMsg(null); setMode("selected"); }}
              className="flex flex-col items-start gap-1 rounded-lg border border-slate-200 p-4 text-left hover:border-teal-300 hover:bg-teal-50 dark:border-slate-700 dark:hover:border-teal-500/40 dark:hover:bg-teal-500/10"
            >
              <span className="font-medium text-slate-800 dark:text-slate-100">Apply for Selected Products</span>
              <span className="text-xs text-slate-500 dark:text-slate-400">
                Different units for different product ranges, by their position (e.g. products
                1-10 get PCS, 11-20 get BOX).
              </span>
            </button>
          </div>
        )}

        {mode === "all" && (
          <div className="flex flex-col gap-4">
            <Input
              label="Unit"
              value={allValue}
              onChange={(e) => setAllValue(e.target.value)}
              placeholder="e.g. PCS"
              autoFocus
            />
            <div className="flex justify-between gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
              <Button variant="secondary" onClick={() => setMode("choice")} disabled={saving}>← Back</Button>
              <Button onClick={applyAll} isLoading={saving}>Apply</Button>
            </div>
          </div>
        )}

        {mode === "selected" && (
          <div className="flex flex-col gap-4">
            <p className="text-xs text-slate-500 dark:text-slate-400">
              From Count / To Count is each product's own position - the same number on its
              "Product N" card - not its Item Quantity. Ranges can't touch: use 1-10 then 11-20,
              not 1-10 then 10-20.
            </p>
            <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-800/50 dark:text-slate-400">
                  <tr>
                    <th className="px-3 py-2 text-left">From Count</th>
                    <th className="px-3 py-2 text-left">To Count</th>
                    <th className="px-3 py-2 text-left">Unit</th>
                    <th className="px-3 py-2 text-left" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {ranges.map((r) => (
                    <tr key={r.id}>
                      <td className="px-3 py-2">
                        <input
                          type="number"
                          value={r.from}
                          onChange={(e) => updateRange(r.id, { from: e.target.value })}
                          placeholder="1"
                          className="w-24 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          type="number"
                          value={r.to}
                          onChange={(e) => updateRange(r.id, { to: e.target.value })}
                          placeholder="10"
                          className="w-24 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          type="text"
                          value={r.unit}
                          onChange={(e) => updateRange(r.id, { unit: e.target.value })}
                          placeholder="PCS"
                          className="w-28 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
                        />
                      </td>
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => removeRange(r.id)}
                          disabled={ranges.length === 1}
                          className="rounded p-1 text-red-500 hover:bg-red-50 disabled:opacity-30 dark:hover:bg-red-500/10"
                          title="Remove row"
                        >
                          🗑
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Button variant="secondary" size="sm" onClick={addRange} className="self-start">+ Add Row</Button>
            <div className="flex justify-between gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
              <Button variant="secondary" onClick={() => setMode("choice")} disabled={saving}>← Back</Button>
              <Button onClick={applySelected} isLoading={saving}>Apply</Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
