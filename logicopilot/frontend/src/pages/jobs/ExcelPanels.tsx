import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import * as jobsApi from "../../api/jobs";
import type { JobDetail } from "../../types/jobs";

/** Excel by sheet (template excel_mode "sheet"): the Unclassified Excel list and the sheet
 *  viewer used on the job page. A sheet has no page images - it is shown as a table. */

export function ExcelSheetModal({
  title,
  load,
  onClose,
}: {
  title: string;
  load: () => Promise<jobsApi.SheetTable[]>;
  onClose: () => void;
}) {
  const [sheets, setSheets] = useState<jobsApi.SheetTable[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(0);

  useEffect(() => {
    let alive = true;
    load()
      .then((s) => alive && setSheets(s))
      .catch((err) => {
        if (!alive) return;
        setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not open the sheet." : "Could not open the sheet.");
      });
    return () => {
      alive = false;
    };
    // Loaded once when the viewer opens - `load` is a new function on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sheet = sheets?.[active];
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full max-w-5xl flex-col rounded-xl bg-white shadow-xl dark:bg-slate-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-700">
          <h3 className="min-w-0 truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{title}</h3>
          <button onClick={onClose} className="ml-3 text-sm text-slate-500 hover:text-slate-800 dark:hover:text-slate-200">
            Close
          </button>
        </div>
        {sheets && sheets.length > 1 && (
          <div className="flex gap-1 overflow-x-auto border-b border-slate-200 px-4 py-2 dark:border-slate-700">
            {sheets.map((s, i) => (
              <button
                key={s.name}
                onClick={() => setActive(i)}
                className={`shrink-0 rounded-md px-2.5 py-1 text-xs ${
                  i === active
                    ? "bg-indigo-100 font-medium text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-200"
                    : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
                }`}
              >
                {s.name}
              </button>
            ))}
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {error && <p className="text-sm text-rose-600">{error}</p>}
          {!error && !sheets && <p className="text-sm text-slate-500">Loading…</p>}
          {sheet && (
            <>
              <table className="border-collapse text-xs">
                <thead>
                  <tr>
                    <th className="sticky top-0 border border-slate-200 bg-slate-100 px-2 py-1 text-slate-500 dark:border-slate-700 dark:bg-slate-800" />
                    {sheet.columns.map((c) => (
                      <th key={c} className="sticky top-0 border border-slate-200 bg-slate-100 px-2 py-1 font-medium text-slate-500 dark:border-slate-700 dark:bg-slate-800">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sheet.rows.map((r) => (
                    <tr key={r.row}>
                      <td className="border border-slate-200 bg-slate-50 px-2 py-1 text-right text-slate-400 dark:border-slate-700 dark:bg-slate-800/60">{r.row}</td>
                      {sheet.columns.map((c) => (
                        <td key={c} className="max-w-[240px] truncate border border-slate-200 px-2 py-1 text-slate-700 dark:border-slate-700 dark:text-slate-200" title={r.cells[c] ?? ""}>
                          {r.cells[c] ?? ""}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {sheet.total_rows > sheet.rows.length && (
                <p className="mt-2 text-xs text-slate-500">
                  Showing the first {sheet.rows.length} of {sheet.total_rows} rows.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function UnclassifiedExcelFiles({
  jobId,
  files,
  slots,
  readOnly,
  onJob,
  onChanged,
}: {
  jobId: string;
  files: jobsApi.UnclassifiedFile[];
  /** The job's document slots, for "Assign to…": template_document_id + name. */
  slots: { id: string; name: string }[];
  readOnly: boolean;
  onJob: (job: JobDetail) => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewing, setViewing] = useState<jobsApi.UnclassifiedFile | null>(null);

  async function assign(file: jobsApi.UnclassifiedFile, tdocId: string, sheet?: string) {
    if (!tdocId) return;
    setBusy(file.id + (sheet ?? ""));
    setError(null);
    try {
      onJob(await jobsApi.assignUnclassifiedFile(jobId, file.id, tdocId, sheet));
      onChanged();
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not assign it." : "Could not assign it.");
    } finally {
      setBusy(null);
    }
  }

  async function remove(file: jobsApi.UnclassifiedFile, sheet?: string) {
    const what = sheet ? `sheet "${sheet}"` : `the whole file ${file.original_filename}`;
    if (!window.confirm(`Remove ${what} from this job?`)) return;
    setBusy(file.id + (sheet ?? ""));
    setError(null);
    try {
      await jobsApi.deleteUnclassifiedFile(jobId, file.id, sheet);
      onChanged();
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not remove it." : "Could not remove it.");
    } finally {
      setBusy(null);
    }
  }

  const slotSelect = (onPick: (id: string) => void, disabled: boolean) => (
    <select
      value=""
      disabled={disabled}
      onChange={(e) => onPick(e.target.value)}
      className="rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
      title="Put this into a document slot - it is then read from its cells"
    >
      <option value="">Assign to…</option>
      {slots.map((s) => (
        <option key={s.id} value={s.id}>{s.name}</option>
      ))}
    </select>
  );

  if (files.length === 0) return null;
  return (
    <div className="mb-3">
      <p className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">
        Excel files not used · {files.length}
      </p>
      {error && <p className="mb-2 text-xs text-rose-600">{error}</p>}
      <div className="flex flex-col gap-2">
        {files.map((f) => (
          <details key={f.id} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900">
            <summary className="flex cursor-pointer items-center gap-2">
              <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300">
                Excel
              </span>
              <span className="min-w-0 flex-1 truncate" title={f.original_filename}>{f.original_filename}</span>
            </summary>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                onClick={() => setViewing(f)}
                className="text-xs text-indigo-600 hover:underline"
              >
                Preview
              </button>
              {!readOnly && slotSelect((id) => assign(f, id), busy !== null)}
              {!readOnly && (
                <button onClick={() => remove(f)} disabled={busy !== null} className="text-xs text-rose-600 hover:underline">
                  Delete whole file
                </button>
              )}
            </div>
            <ul className="mt-2 space-y-1">
              {f.sheets.map((sh) => (
                <li key={sh.name} className="flex flex-wrap items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
                  <span className="font-medium">{sh.name}</span>
                  <span className="text-slate-400">{sh.rows} row{sh.rows === 1 ? "" : "s"}</span>
                  {sh.reason && <span className="text-slate-400">· {sh.reason}</span>}
                  {!readOnly && f.sheets.length > 1 && slotSelect((id) => assign(f, id, sh.name), busy !== null)}
                  {!readOnly && f.sheets.length > 1 && (
                    <button onClick={() => remove(f, sh.name)} disabled={busy !== null} className="text-rose-600 hover:underline" title="Remove this sheet">
                      🗑
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </details>
        ))}
      </div>
      {viewing && (
        <ExcelSheetModal
          title={viewing.original_filename}
          load={() => jobsApi.previewUnclassifiedFile(jobId, viewing.id)}
          onClose={() => setViewing(null)}
        />
      )}
    </div>
  );
}


/** What the reviewer is looking at on the right: the focused value, and its line for a line item. */
export type SheetFocus = { fieldId: string; value: string; rowIndex: number | null } | null;

function norm(v: string) {
  return v.replace(/\s+/g, " ").trim().toUpperCase();
}

function asNumber(v: string): number | null {
  const t = v.replace(/[,\s]/g, "");
  return /^-?\d*\.?\d+$/.test(t) ? Number(t) : null;
}

/** Cells holding the value: exact (ignoring case, spaces and number formatting) first, then
 *  cells that contain it. A line item (rowIndex) picks its own occurrence when there are several. */
function findCells(sheet: jobsApi.SheetTable, value: string, rowIndex: number | null): { row: number; col: string } | null {
  const want = norm(value);
  if (!want) return null;
  const wantNum = asNumber(want);
  const exact: { row: number; col: string }[] = [];
  const partial: { row: number; col: string }[] = [];
  for (const r of sheet.rows) {
    for (const c of sheet.columns) {
      const cell = norm(r.cells[c] ?? "");
      if (!cell) continue;
      const cellNum = asNumber(cell);
      if (cell === want || (wantNum !== null && cellNum !== null && Math.abs(cellNum - wantNum) < 1e-6)) exact.push({ row: r.row, col: c });
      else if (want.length >= 3 && cell.includes(want)) partial.push({ row: r.row, col: c });
    }
  }
  const hits = exact.length ? exact : partial;
  if (!hits.length) return null;
  if (rowIndex != null && hits.length > 1) {
    const rows = [...new Set(hits.map((h) => h.row))];
    const row = rows.at(Math.min(rowIndex, rows.length) - 1);
    return hits.find((h) => h.row === row) ?? hits[0];
  }
  return hits[0];
}

/** An Excel document on the review screen: its cells as a grid where a PDF shows its page, the
 *  focused value's cell highlighted and scrolled to. Read-only; nothing is converted. */
export function ExcelSheetViewer({
  jobId, docId, title, updatedAt, focus,
}: {
  jobId: string;
  docId: string;
  title: string;
  updatedAt?: string;
  focus?: SheetFocus;
}) {
  const [sheet, setSheet] = useState<jobsApi.SheetTable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState(12);
  const [full, setFull] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    setSheet(null);
    setError(null);
    jobsApi.getDocumentSheet(jobId, docId)
      .then((s) => alive && setSheet(s))
      .catch((err) => alive && setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not open the sheet." : "Could not open the sheet."));
    return () => { alive = false; };
  }, [jobId, docId, updatedAt]);

  const hit = useMemo(() => (sheet && focus ? findCells(sheet, focus.value, focus.rowIndex) : null), [sheet, focus?.fieldId, focus?.value, focus?.rowIndex]);

  useEffect(() => {
    if (!hit) return;
    const el = boxRef.current?.querySelector<HTMLElement>(`[data-cell="${hit.col}${hit.row}"]`);
    el?.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
  }, [hit]);

  async function download() {
    setDownloading(true);
    try {
      await jobsApi.downloadDocumentFile(jobId, docId, title.split(" - ")[0] || "sheet.xlsx");
    } catch {
      setError("Could not download the original file.");
    } finally {
      setDownloading(false);
    }
  }

  const btn = "rounded px-2 py-1 text-xs text-slate-600 hover:bg-slate-200 dark:text-slate-300 dark:hover:bg-slate-700";
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-100 px-2 py-1.5 dark:bg-slate-800">
        <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[11px] font-semibold text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300">Excel</span>
        <span className="min-w-0 flex-1 truncate text-xs text-slate-600 dark:text-slate-300" title={title}>{sheet?.name ?? title}</span>
        <button type="button" className={btn} onClick={() => setSize((n) => Math.max(9, n - 1))} title="Smaller">A−</button>
        <button type="button" className={btn} onClick={() => setSize((n) => Math.min(18, n + 1))} title="Larger">A+</button>
        <button type="button" className={btn} onClick={() => setFull(true)}>Open full size</button>
        <button type="button" className={btn} onClick={download} disabled={downloading}>{downloading ? "Downloading…" : "Download original"}</button>
      </div>
      {focus && sheet && !hit && (
        <p className="text-xs text-amber-700 dark:text-amber-300">"{focus.value}" was not found as it is in this sheet - it may have been reformatted or worked out from several cells.</p>
      )}
      <div ref={boxRef} className="max-h-[70vh] overflow-auto rounded-lg border border-slate-200 dark:border-slate-700">
        {error && <p className="p-3 text-sm text-rose-600">{error}</p>}
        {!error && !sheet && <p className="p-3 text-sm text-slate-500">Loading the sheet…</p>}
        {sheet && (
          <table className="border-collapse" style={{ fontSize: `${size}px` }}>
            <thead>
              <tr>
                <th className="sticky left-0 top-0 z-20 border border-slate-200 bg-slate-100 px-2 py-1 dark:border-slate-700 dark:bg-slate-800" />
                {sheet.columns.map((c) => (
                  <th key={c} className={`sticky top-0 z-10 border border-slate-200 px-2 py-1 font-medium dark:border-slate-700 ${hit?.col === c ? "bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-200" : "bg-slate-100 text-slate-500 dark:bg-slate-800"}`}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sheet.rows.map((r) => {
                const onRow = hit?.row === r.row;
                return (
                  <tr key={r.row} className={onRow ? "bg-amber-50 dark:bg-amber-500/10" : ""}>
                    <td className={`sticky left-0 z-10 border border-slate-200 px-2 py-1 text-right dark:border-slate-700 ${onRow ? "bg-amber-100 font-semibold text-amber-800 dark:bg-amber-500/20 dark:text-amber-200" : "bg-slate-50 text-slate-400 dark:bg-slate-800"}`}>{r.row}</td>
                    {sheet.columns.map((c) => {
                      const on = onRow && hit?.col === c;
                      return (
                        <td key={c} data-cell={`${c}${r.row}`}
                          className={`max-w-[260px] truncate border border-slate-200 px-2 py-1 dark:border-slate-700 ${on ? "bg-amber-200 font-semibold text-slate-900 outline outline-2 outline-amber-500 dark:bg-amber-500/40 dark:text-white" : "text-slate-700 dark:text-slate-200"}`}
                          title={r.cells[c] ?? ""}>
                          {r.cells[c] ?? ""}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      {sheet && sheet.total_rows > sheet.rows.length && (
        <p className="text-xs text-slate-500">Showing the first {sheet.rows.length} of {sheet.total_rows} rows - Download original for the rest.</p>
      )}
      {full && (
        <ExcelSheetModal title={title} load={async () => [await jobsApi.getDocumentSheet(jobId, docId)]} onClose={() => setFull(false)} />
      )}
    </div>
  );
}
