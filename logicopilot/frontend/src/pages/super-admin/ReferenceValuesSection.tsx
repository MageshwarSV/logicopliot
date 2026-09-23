import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { Button } from "../../components/ui/Button";
import { Select } from "../../components/ui/Input";
import * as api from "../../api/onboarding";
import type { ReferenceOwnerType, ReferenceUploadConflict, ReferenceValueRow } from "../../api/onboarding";
import type { TemplateGroup } from "../../types/onboarding";

// One entry per is_target_value field found across every template group - a mark (under a
// document) or a custom field, flattened into one pickable list since both are managed the
// exact same way from here.
interface FieldOption {
  ownerType: ReferenceOwnerType;
  ownerId: string;
  label: string; // "Sea Import · Invoice · Quotation" or "Sea Import · Custom: Quotation"
}

/** Manage the reference table behind an is_target_value field: what's been learned so far,
 *  a template to fill in offline, and a bulk upload with the same de-duplication and
 *  conflict handling a single correction already gets - a key already on file with a
 *  DIFFERENT value is never silently overwritten, only reported, so this screen can ask
 *  which one to keep instead of guessing for someone who never got asked. */
export function ReferenceValuesSection() {
  const [options, setOptions] = useState<FieldOption[]>([]);
  const [loadingOptions, setLoadingOptions] = useState(true);
  const [selected, setSelected] = useState<FieldOption | null>(null);
  const [rows, setRows] = useState<ReferenceValueRow[]>([]);
  const [loadingRows, setLoadingRows] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<{ added: number; unchanged: number; skipped: number } | null>(null);
  const [conflicts, setConflicts] = useState<ReferenceUploadConflict[]>([]);
  const [resolvingId, setResolvingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Every group's own target-value fields, gathered once - this screen is an occasional
  // admin task, not a hot path, so a handful of extra requests up front is the simplest way
  // to build one flat picker instead of a two-step "group, then field" navigation.
  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoadingOptions(true);
      try {
        const groups: TemplateGroup[] = await api.listGroups();
        const found: FieldOption[] = [];
        for (const g of groups) {
          const detail = await api.getGroup(g.id);
          for (const cf of detail.custom_fields ?? []) {
            if (cf.is_target_value) {
              found.push({ ownerType: "custom-fields", ownerId: cf.id, label: `${g.name} · Custom: ${cf.label_name}` });
            }
          }
          for (const doc of detail.documents ?? []) {
            for (const m of doc.marks ?? []) {
              if (m.is_target_value) {
                found.push({ ownerType: "marks", ownerId: m.id, label: `${g.name} · ${doc.name} · ${m.label_name}` });
              }
            }
          }
        }
        if (!cancelled) setOptions(found);
      } catch {
        if (!cancelled) setError("Could not load target-value fields.");
      } finally {
        if (!cancelled) setLoadingOptions(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  async function selectField(opt: FieldOption | null) {
    setSelected(opt);
    setUploadResult(null);
    setConflicts([]);
    setError(null);
    if (!opt) {
      setRows([]);
      return;
    }
    setLoadingRows(true);
    try {
      setRows(await api.listReferenceValues(opt.ownerType, opt.ownerId));
    } catch {
      setError("Could not load reference values for this field.");
    } finally {
      setLoadingRows(false);
    }
  }

  async function refreshRows() {
    if (!selected) return;
    try {
      setRows(await api.listReferenceValues(selected.ownerType, selected.ownerId));
    } catch {
      // leave the table as it was rather than clear it over a refresh hiccup
    }
  }

  async function downloadTemplate() {
    if (!selected) return;
    try {
      const { url, filename } = await api.downloadReferenceValuesTemplate(selected.ownerType, selected.ownerId);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError("Could not download the template.");
    }
  }

  async function handleUpload(file: File) {
    if (!selected) return;
    setUploading(true);
    setError(null);
    setUploadResult(null);
    setConflicts([]);
    try {
      const result = await api.uploadReferenceValues(selected.ownerType, selected.ownerId, file);
      setUploadResult({ added: result.rows_added, unchanged: result.rows_unchanged, skipped: result.rows_skipped });
      setConflicts(result.conflicts);
      await refreshRows();
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Upload failed." : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  async function resolveConflict(c: ReferenceUploadConflict, useUploaded: boolean) {
    if (!selected) return;
    if (!useUploaded) {
      // "Keep the old value" needs no call at all - just stop offering this conflict.
      setConflicts((prev) => prev.filter((x) => x.s_no !== c.s_no));
      return;
    }
    setResolvingId(c.s_no);
    try {
      await api.updateReferenceValue(selected.ownerType, selected.ownerId, c.s_no, c.uploaded_value);
      setConflicts((prev) => prev.filter((x) => x.s_no !== c.s_no));
      await refreshRows();
    } catch {
      setError("Could not apply that value.");
    } finally {
      setResolvingId(null);
    }
  }

  async function removeRow(row: ReferenceValueRow) {
    if (!selected) return;
    if (!window.confirm(`Remove this learned value ("${row.resolved_value}")? It will be asked for again next time.`)) return;
    try {
      await api.deleteReferenceValue(selected.ownerType, selected.ownerId, row.s_no);
      await refreshRows();
    } catch {
      setError("Could not remove that row.");
    }
  }

  const matchColumnsUsed = useMemo(() => {
    // Most target-value fields only ever use slot 1 (they key on their own single extracted
    // value); a kind="lookup" field can use up to 4. Show only the slots that actually carry
    // anything across the current rows, so a single-slot field's table isn't three empty
    // columns wide.
    const used = [false, false, false, false];
    for (const r of rows) {
      if (r.match_value_1) used[0] = true;
      if (r.match_value_2) used[1] = true;
      if (r.match_value_3) used[2] = true;
      if (r.match_value_4) used[3] = true;
    }
    if (!used.some(Boolean)) used[0] = true;
    return used;
  }, [rows]);

  return (
    <div className="border-t border-slate-200 px-5 py-5 dark:border-slate-800">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">Target-value reference tables</h3>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Every field ticked "This is a target value?" in the template wizard keeps its own table
        here — download a template, fill it in offline, and upload it in bulk instead of typing
        one row at a time. A key already on file with a different value is never silently
        overwritten; you're asked which one to keep.
      </p>

      {loadingOptions && <p className="mt-3 text-xs text-slate-400">Loading fields…</p>}

      {!loadingOptions && options.length === 0 && (
        <p className="mt-3 text-xs text-slate-400">
          No field is ticked "This is a target value?" yet — set one up in the template wizard first.
        </p>
      )}

      {!loadingOptions && options.length > 0 && (
        <div className="mt-3 max-w-md">
          <Select
            label="Field"
            value={selected ? `${selected.ownerType}:${selected.ownerId}` : ""}
            onChange={(e) => {
              const val = e.target.value;
              selectField(val ? options.find((o) => `${o.ownerType}:${o.ownerId}` === val) ?? null : null);
            }}
          >
            <option value="">Choose a field…</option>
            {options.map((o) => (
              <option key={`${o.ownerType}:${o.ownerId}`} value={`${o.ownerType}:${o.ownerId}`}>
                {o.label}
              </option>
            ))}
          </Select>
        </div>
      )}

      {selected && (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onClick={downloadTemplate}>Download template</Button>
            <label className="inline-flex cursor-pointer items-center rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800">
              {uploading ? "Uploading…" : "Upload filled-in template"}
              <input
                type="file"
                accept=".xlsx,.xlsm"
                className="hidden"
                disabled={uploading}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) handleUpload(file);
                }}
              />
            </label>
          </div>

          {uploadResult && (
            <p className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
              {uploadResult.added} added, {uploadResult.unchanged} already matched what was on file, {uploadResult.skipped} row(s) skipped (blank).
              {conflicts.length > 0 && ` ${conflicts.length} need a decision below.`}
            </p>
          )}

          {conflicts.length > 0 && (
            <div className="overflow-x-auto rounded-lg border border-amber-200 dark:border-amber-500/30">
              <table className="w-full text-left text-xs">
                <thead className="bg-amber-50 dark:bg-amber-500/10">
                  <tr>
                    <th className="px-3 py-2 font-medium text-amber-800 dark:text-amber-300">Key</th>
                    <th className="px-3 py-2 font-medium text-amber-800 dark:text-amber-300">Already on file</th>
                    <th className="px-3 py-2 font-medium text-amber-800 dark:text-amber-300">Uploaded</th>
                    <th className="px-3 py-2 font-medium text-amber-800 dark:text-amber-300">Keep which?</th>
                  </tr>
                </thead>
                <tbody>
                  {conflicts.map((c) => (
                    <tr key={c.s_no} className="border-t border-amber-100 dark:border-amber-500/20">
                      <td className="px-3 py-2 text-slate-700 dark:text-slate-300">
                        {[c.match_value_1, c.match_value_2, c.match_value_3, c.match_value_4].filter(Boolean).join(" / ")}
                      </td>
                      <td className="px-3 py-2 text-slate-700 dark:text-slate-300">{c.existing_value}</td>
                      <td className="px-3 py-2 text-slate-700 dark:text-slate-300">{c.uploaded_value}</td>
                      <td className="px-3 py-2">
                        <div className="flex gap-1.5">
                          <Button size="sm" variant="secondary" disabled={resolvingId === c.s_no} onClick={() => resolveConflict(c, false)}>
                            Keep existing
                          </Button>
                          <Button size="sm" disabled={resolvingId === c.s_no} onClick={() => resolveConflict(c, true)}>
                            {resolvingId === c.s_no ? "Applying…" : "Use uploaded"}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 dark:bg-slate-800/50">
                <tr>
                  {matchColumnsUsed[0] && <th className="px-3 py-2 font-medium text-slate-500 dark:text-slate-400">Match 1</th>}
                  {matchColumnsUsed[1] && <th className="px-3 py-2 font-medium text-slate-500 dark:text-slate-400">Match 2</th>}
                  {matchColumnsUsed[2] && <th className="px-3 py-2 font-medium text-slate-500 dark:text-slate-400">Match 3</th>}
                  {matchColumnsUsed[3] && <th className="px-3 py-2 font-medium text-slate-500 dark:text-slate-400">Match 4</th>}
                  <th className="px-3 py-2 font-medium text-slate-500 dark:text-slate-400">Resolved value</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {loadingRows && (
                  <tr><td className="px-3 py-3 text-slate-400" colSpan={6}>Loading…</td></tr>
                )}
                {!loadingRows && rows.length === 0 && (
                  <tr><td className="px-3 py-3 text-slate-400" colSpan={6}>Nothing learned for this field yet.</td></tr>
                )}
                {!loadingRows && rows.map((r) => (
                  <tr key={r.s_no} className="border-t border-slate-100 dark:border-slate-800">
                    {matchColumnsUsed[0] && <td className="px-3 py-2 text-slate-700 dark:text-slate-300">{r.match_value_1}</td>}
                    {matchColumnsUsed[1] && <td className="px-3 py-2 text-slate-700 dark:text-slate-300">{r.match_value_2}</td>}
                    {matchColumnsUsed[2] && <td className="px-3 py-2 text-slate-700 dark:text-slate-300">{r.match_value_3}</td>}
                    {matchColumnsUsed[3] && <td className="px-3 py-2 text-slate-700 dark:text-slate-300">{r.match_value_4}</td>}
                    <td className="px-3 py-2 text-slate-700 dark:text-slate-300">{r.resolved_value}</td>
                    <td className="px-3 py-2 text-right">
                      <button
                        type="button"
                        onClick={() => removeRow(r)}
                        className="text-rose-600 hover:underline dark:text-rose-400"
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {error && (
        <p className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </p>
      )}
    </div>
  );
}
