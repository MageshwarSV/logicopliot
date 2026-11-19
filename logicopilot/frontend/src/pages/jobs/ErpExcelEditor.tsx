import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import * as jobsApi from "../../api/jobs";
import { formatDateTime } from "../../utils/dateTime";

/** The job's ERP-import Excel, opened and edited inside the application: every sheet as a grid,
 *  cells editable, rows can be added or removed. Save keeps it as the job's workbook - from then
 *  on Download as Excel and the real ERP import (Final Submit) use it, until "Reset to generated". */
function colName(i: number): string {
  let s = "";
  let n = i + 1;
  while (n) { const r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = Math.floor((n - 1) / 26); }
  return s;
}

export function ErpExcelEditor({
  jobId, readOnly: userReadOnly, onClose, onSaved,
}: { jobId: string; readOnly: boolean; onClose: () => void; onSaved: () => void }) {
  const [locked, setLocked] = useState<string | null>(null);
  const readOnly = userReadOnly || !!locked;
  const [sheets, setSheets] = useState<jobsApi.ErpExcelSheet[] | null>(null);
  const [edited, setEdited] = useState<jobsApi.ErpExcelEdited | null>(null);
  const [file, setFile] = useState("");
  const [active, setActive] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<"save" | "reset" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [onlyFilled, setOnlyFilled] = useState(true);

  async function load() {
    setError(null);
    try {
      const d = await jobsApi.openJobExcel(jobId);
      setSheets(d.sheets);
      setEdited(d.edited);
      setFile(d.file);
      setLocked(d.locked ?? null);
      setDirty(false);
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not open the Excel." : "Could not open the Excel.");
    }
  }
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [jobId]);

  const sheet = sheets?.[active];
  const width = useMemo(() => Math.max(0, ...(sheet?.rows ?? []).map((r) => r.length)), [sheet]);

  function update(fn: (rows: string[][]) => string[][]) {
    if (!sheets) return;
    setSheets(sheets.map((s, i) => (i === active ? { ...s, rows: fn(s.rows.map((r) => [...r])) } : s)));
    setDirty(true);
  }
  const setCell = (r: number, c: number, v: string) => update((rows) => {
    while (rows[r].length <= c) rows[r].push("");
    rows[r][c] = v;
    return rows;
  });
  const addRow = () => update((rows) => [...rows, Array(Math.max(width, 1)).fill("")]);
  const delRow = (r: number) => update((rows) => rows.filter((_, i) => i !== r));

  async function save() {
    if (!sheets) return;
    setBusy("save");
    setError(null);
    try {
      const d = await jobsApi.saveJobExcel(jobId, sheets);
      setEdited(d.edited);
      setDirty(false);
      onSaved();
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not save." : "Could not save.");
    } finally {
      setBusy(null);
    }
  }
  async function reset() {
    if (!window.confirm("Throw away the edited Excel and build it again from the job's data?")) return;
    setBusy("reset");
    try {
      await jobsApi.resetJobExcel(jobId);
      await load();
      onSaved();
    } finally {
      setBusy(null);
    }
  }
  function close() {
    if (dirty && !window.confirm("You have unsaved changes. Close without saving?")) return;
    onClose();
  }

  const nonEmpty = (s: jobsApi.ErpExcelSheet) => s.rows.slice(1).some((r) => r.some((c) => c.trim()));
  const tabs = (sheets ?? []).map((s, i) => ({ s, i })).filter(({ s, i }) => !onlyFilled || nonEmpty(s) || i === active);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-white dark:bg-slate-900">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-700">
        <div className="min-w-0">
          <h3 className="truncate text-base font-semibold text-slate-900 dark:text-slate-50">ERP Excel — {file || "…"}</h3>
          <p className="text-xs text-slate-500">
            {edited
              ? `Edited copy in use — saved${edited.saved_by ? ` by ${edited.saved_by}` : ""}${edited.saved_at ? ` on ${formatDateTime(edited.saved_at)}` : ""}. Download and Final Submit use it.`
              : "Built from the job's data. Saving makes your edited copy the one used for Download and Final Submit."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-xs text-slate-500">
            <input type="checkbox" checked={onlyFilled} onChange={(e) => setOnlyFilled(e.target.checked)} /> Only sheets with data
          </label>
          {!readOnly && edited && (
            <button onClick={reset} disabled={busy !== null} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-sm font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50">
              {busy === "reset" ? "Resetting…" : "Reset to generated"}
            </button>
          )}
          {!readOnly && (
            <button onClick={save} disabled={busy !== null || !dirty} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-40">
              {busy === "save" ? "Saving…" : dirty ? "Save" : "Saved"}
            </button>
          )}
          <button onClick={close} className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200">Close</button>
        </div>
      </div>
      {locked && (
        <p className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          {locked} You can still view and download it.
        </p>
      )}
      {error && <p className="px-4 py-2 text-sm text-rose-600">{error}</p>}
      <div className="flex gap-1 overflow-x-auto border-b border-slate-200 px-4 py-2 dark:border-slate-700">
        {tabs.map(({ s, i }) => (
          <button key={s.name} onClick={() => setActive(i)}
            className={`shrink-0 rounded-md px-2.5 py-1 text-xs ${i === active ? "bg-indigo-100 font-semibold text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-200" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
            {s.name}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {!sheets && !error && <p className="p-4 text-sm text-slate-500">Opening…</p>}
        {sheet && (
          <table className="border-collapse text-xs">
            <thead>
              <tr>
                <th className="sticky left-0 top-0 z-20 border border-slate-200 bg-slate-100 px-2 py-1 dark:border-slate-700 dark:bg-slate-800" />
                {Array.from({ length: width }, (_, c) => (
                  <th key={c} className="sticky top-0 z-10 border border-slate-200 bg-slate-100 px-2 py-1 font-medium text-slate-500 dark:border-slate-700 dark:bg-slate-800">{colName(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sheet.rows.map((row, r) => (
                <tr key={r} className={r === 0 ? "bg-slate-50 font-semibold dark:bg-slate-800/60" : ""}>
                  <td className="sticky left-0 z-10 border border-slate-200 bg-slate-50 px-1 text-right text-slate-400 dark:border-slate-700 dark:bg-slate-800">
                    <span className="inline-flex items-center gap-1">
                      {r + 1}
                      {!readOnly && r > 0 && (
                        <button onClick={() => delRow(r)} title="Delete this row" className="text-rose-500 hover:text-rose-700">×</button>
                      )}
                    </span>
                  </td>
                  {Array.from({ length: width }, (_, c) => (
                    <td key={c} className="border border-slate-200 p-0 dark:border-slate-700">
                      <input
                        value={row[c] ?? ""}
                        readOnly={readOnly || r === 0}
                        onChange={(e) => setCell(r, c, e.target.value)}
                        title={row[c] ?? ""}
                        className={`w-36 bg-transparent px-1.5 py-1 text-slate-800 outline-none dark:text-slate-100 ${r === 0 ? "cursor-default font-semibold" : "focus:bg-indigo-50 dark:focus:bg-indigo-500/10"}`}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {sheet && !readOnly && (
          <button onClick={addRow} className="m-3 rounded-md border border-dashed border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300">
            + Add row
          </button>
        )}
      </div>
    </div>
  );
}
