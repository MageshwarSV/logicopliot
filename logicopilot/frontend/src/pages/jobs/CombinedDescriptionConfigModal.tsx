import { useEffect, useMemo, useState } from "react";
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
  onSaved?: (fieldValues: jobsApi.CompositeFieldConfigSaveResult["field_values"]) => void;
}

export function CombinedDescriptionConfigModal({
  open, onClose, groupId, defaultConsignee, jobId, onSaved,
}: Props) {
  const [consignees, setConsignees] = useState<string[]>([]);
  const [consignee, setConsignee] = useState("");
  const [labelName, setLabelName] = useState("Combined Description");
  const [rows, setRows] = useState<ConfigRow[]>([]);
  const [availableLabels, setAvailableLabels] = useState<string[]>([]);
  const [sampleRows, setSampleRows] = useState<Record<string, string>[]>([]);
  const [loadingConsignees, setLoadingConsignees] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

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
      setRows([]);
      setAvailableLabels([]);
      setSampleRows([]);
      return;
    }
    setLoadingConfig(true);
    setError(null);
    jobsApi.getCompositeFieldConfig(groupId, consignee, labelName)
      .then((cfg) => {
        setRows(toRows(cfg.pieces, cfg.separators));
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

  const unusedLabels = useMemo(
    () => availableLabels.filter((l) => !rows.some((r) => r.kind === "field" && r.fieldLabel === l)),
    [availableLabels, rows],
  );
  const previewSample = sampleRows[0] ?? {};
  const liveOutput = joinRows(rows, previewSample);

  function updateRow(i: number, patch: Partial<ConfigRow>) {
    setRows((arr) => arr.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }
  function removeRow(i: number) {
    setRows((arr) => arr.filter((_, idx) => idx !== i));
  }
  function addRow() {
    const firstUnused = unusedLabels[0];
    setRows((arr) => [
      ...arr,
      firstUnused
        ? { kind: "field", fieldLabel: firstUnused, fixedText: "", separator: "" }
        : { kind: "fixed", fieldLabel: "", fixedText: "", separator: "" },
    ]);
  }
  function move(i: number, dir: -1 | 1) {
    setRows((arr) => {
      const next = [...arr];
      const j = i + dir;
      if (j < 0 || j >= next.length) return arr;
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }

  async function handleSave() {
    if (!consignee.trim()) {
      setError("Pick a consignee first.");
      return;
    }
    if (rows.length === 0) {
      setError("Add at least one row.");
      return;
    }
    for (const row of rows) {
      if (row.kind === "fixed" && !row.fixedText.trim()) {
        setError("A fixed-text row cannot be empty.");
        return;
      }
      if (row.kind === "field" && !row.fieldLabel) {
        setError("Choose a source field for every row, or remove it.");
        return;
      }
    }
    setSaving(true);
    setError(null);
    setSavedNote(null);
    try {
      const pieces: jobsApi.CompositePiece[] = rows.map((r) =>
        r.kind === "fixed" ? { fixed: r.fixedText.trim() } : r.fieldLabel,
      );
      const separators = rows.map((r) => r.separator);
      const result = await jobsApi.saveCompositeFieldConfig(groupId, {
        consignee: consignee.trim(), labelName, pieces, separators, jobId,
      });
      setSavedNote(`Configuration saved for ${consignee.trim()}`);
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

  async function copyOutput() {
    try {
      await navigator.clipboard.writeText(liveOutput);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be blocked (permissions, insecure context) - nothing worth
      // surfacing as an error for a convenience button.
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Combined Description Configuration" maxWidth="max-w-4xl">
      <div className="flex flex-col gap-5">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Set the format for combining fields. This configuration is saved at the consignee
          level and will be used for all future jobs of this consignee.
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
            <div>
              <div className="mb-2 flex items-center justify-between">
                <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                  Field Order and Format
                </p>
                <Button variant="secondary" size="sm" onClick={addRow}>+ Add Row</Button>
              </div>
              {rows.length === 0 ? (
                <p className="rounded-lg border border-dashed border-slate-300 p-4 text-center text-xs text-slate-400 dark:border-slate-700">
                  No rows yet - add one to start building the format.
                </p>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-800/50 dark:text-slate-400">
                      <tr>
                        <th className="px-3 py-2 text-left">Order</th>
                        <th className="px-3 py-2 text-left">Source Field</th>
                        <th className="px-3 py-2 text-left">Separator (before)</th>
                        <th className="px-3 py-2 text-left">Example Value</th>
                        <th className="px-3 py-2 text-left">Preview Piece</th>
                        <th className="px-3 py-2 text-left">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                      {rows.map((row, i) => {
                        const value = rowValue(row, previewSample);
                        return (
                          <tr key={i}>
                            <td className="px-3 py-2">
                              <div className="flex items-center gap-1">
                                <span className="flex h-6 w-6 items-center justify-center rounded bg-slate-100 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                                  {i + 1}
                                </span>
                                <button type="button" disabled={i === 0} onClick={() => move(i, -1)}
                                  className="rounded px-1 text-slate-400 hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-slate-800" title="Move up">▲</button>
                                <button type="button" disabled={i === rows.length - 1} onClick={() => move(i, 1)}
                                  className="rounded px-1 text-slate-400 hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-slate-800" title="Move down">▼</button>
                              </div>
                            </td>
                            <td className="px-3 py-2">
                              <select
                                value={row.kind === "fixed" ? "__fixed__" : row.fieldLabel}
                                onChange={(e) => {
                                  const v = e.target.value;
                                  if (v === "__fixed__") updateRow(i, { kind: "fixed", fieldLabel: "" });
                                  else updateRow(i, { kind: "field", fieldLabel: v, fixedText: "" });
                                }}
                                className="w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
                              >
                                <option value="__fixed__">Fixed Text</option>
                                {row.kind === "field" && row.fieldLabel && !availableLabels.includes(row.fieldLabel) && (
                                  <option value={row.fieldLabel}>{row.fieldLabel}</option>
                                )}
                                {availableLabels
                                  .filter((l) => l === row.fieldLabel || unusedLabels.includes(l))
                                  .map((l) => (
                                    <option key={l} value={l}>{l}</option>
                                  ))}
                              </select>
                            </td>
                            <td className="px-3 py-2">
                              <select
                                value={row.separator}
                                onChange={(e) => updateRow(i, { separator: e.target.value })}
                                className="w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
                              >
                                {SEPARATOR_OPTIONS.map((opt) => (
                                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                                ))}
                              </select>
                            </td>
                            <td className="px-3 py-2">
                              {row.kind === "fixed" ? (
                                <input
                                  type="text"
                                  value={row.fixedText}
                                  onChange={(e) => updateRow(i, { fixedText: e.target.value })}
                                  placeholder="e.g. ("
                                  className="w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
                                />
                              ) : (
                                <span className="text-slate-500 dark:text-slate-400">{value || "—"}</span>
                              )}
                            </td>
                            <td className="px-3 py-2">
                              <span className="rounded bg-emerald-50 px-2 py-1 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
                                {value || "—"}
                              </span>
                            </td>
                            <td className="px-3 py-2">
                              <button type="button" onClick={() => removeRow(i)}
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
            </div>

            <div>
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-400">
                Live Output (updates automatically)
              </p>
              <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 dark:border-emerald-500/20 dark:bg-emerald-500/10">
                <span className="flex-1 font-medium text-emerald-900 dark:text-emerald-100">
                  {liveOutput || "—"}
                </span>
                <button type="button" onClick={copyOutput}
                  className="rounded-md border border-emerald-300 bg-white px-2 py-1 text-xs text-emerald-700 hover:bg-emerald-50 dark:border-emerald-500/30 dark:bg-slate-900 dark:text-emerald-300">
                  {copied ? "Copied!" : "Copy"}
                </button>
              </div>
            </div>

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
