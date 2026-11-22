import { useEffect, useMemo, useState, type ReactNode, type SVGProps } from "react";
import axios from "axios";
import * as jobsApi from "../../api/jobs";
import { formatDateTime } from "../../utils/dateTime";
import { ArrowPathIcon, ArrowRightIcon, CheckCircleIcon } from "../../components/ui/icons";

/** The job's ERP-import Excel, opened and edited inside the application: every sheet as a grid,
 *  cells editable (the header row is locked - the ERP import maps columns by it), rows can be
 *  added or removed. Save keeps it as the job's workbook - from then on Download ERP Excel and
 *  the real ERP import use it, until "Reset to generated". Submit runs the page's own Submit
 *  (same checks), saving first. */

export interface ErpExcelSubmit {
  label: string;
  disabled: boolean;
  /** Why it can't be used right now, shown as the tooltip. */
  hint?: string;
  run: () => void;
}

const ZOOMS = [0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.25, 1.5];
const ZOOM_KEY = "erpExcelEditor.zoom";

function colName(i: number): string {
  let s = "";
  let n = i + 1;
  while (n) { const r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = Math.floor((n - 1) / 26); }
  return s;
}

const svg = (props: SVGProps<SVGSVGElement>, d: ReactNode) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden {...props}>{d}</svg>
);
const SaveIcon = (p: SVGProps<SVGSVGElement>) => svg(p, <><path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V3z" /><path d="M8 3v5h7V3" /><rect x="8" y="13" width="8" height="5" rx="1" /></>);
const XIcon = (p: SVGProps<SVGSVGElement>) => svg(p, <path d="M6 6l12 12M18 6L6 18" />);
const ZoomInIcon = (p: SVGProps<SVGSVGElement>) => svg(p, <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2M11 8.5v5M8.5 11h5" /></>);
const ZoomOutIcon = (p: SVGProps<SVGSVGElement>) => svg(p, <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2M8.5 11h5" /></>);
const TableIcon = (p: SVGProps<SVGSVGElement>) => svg(p, <><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><path d="M3.5 9.5h17M3.5 14.5h17M9.5 9.5v10" /></>);
const FunnelIcon = (p: SVGProps<SVGSVGElement>) => svg(p, <path d="M4 5h16l-6 7.5V18l-4 2v-7.5L4 5z" />);

/** Per sheet: column -> the values still shown (a column without an entry is not filtered). */
type ColFilters = Record<number, string[]>;

const cellKey = (v: string | undefined) => (v ?? "").trim();

/** Excel-style column filter: search, (Select all), one checkbox per distinct value. */
function FilterMenu({ values, allowed, onApply, onClose }: {
  values: string[]; allowed: string[] | undefined; onApply: (allowed: string[] | null) => void; onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Set<string>>(() => new Set(allowed ?? values));
  const needle = q.trim().toLowerCase();
  const shown = values.filter((v) => (v || "(Blanks)").toLowerCase().includes(needle));
  const allShown = shown.length > 0 && shown.every((v) => picked.has(v));
  const chosen = values.filter((v) => picked.has(v) && (!needle || shown.includes(v)));
  const toggle = (v: string) => setPicked((p) => { const n = new Set(p); if (n.has(v)) n.delete(v); else n.add(v); return n; });
  const toggleAll = () => setPicked((p) => { const n = new Set(p); shown.forEach((v) => (allShown ? n.delete(v) : n.add(v))); return n; });
  const apply = () => { if (chosen.length) onApply(chosen.length === values.length ? null : chosen); };
  const row = "flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 hover:bg-slate-100 dark:hover:bg-slate-700";
  return (
    <>
      <div className="fixed inset-0 z-40 cursor-default" onClick={onClose} />
      <div className="absolute left-0 top-full z-50 mt-1 w-64 rounded-lg border border-slate-200 bg-white p-2 text-left text-xs font-normal text-slate-700 shadow-xl dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200">
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search"
          onKeyDown={(e) => { if (e.key === "Enter") apply(); }}
          className="mb-1.5 w-full rounded-md border border-slate-200 bg-white px-2 py-1 outline-none focus:ring-2 focus:ring-indigo-400 dark:border-slate-600 dark:bg-slate-900" />
        <label className={`${row} font-medium`}>
          <input type="checkbox" checked={allShown} onChange={toggleAll} /> (Select all{needle ? " shown" : ""})
        </label>
        <div className="max-h-56 overflow-auto border-t border-slate-100 pt-1 dark:border-slate-700">
          {shown.map((v) => (
            <label key={v} className={row}>
              <input type="checkbox" checked={picked.has(v)} onChange={() => toggle(v)} />
              <span className={`truncate ${v ? "" : "italic text-slate-400"}`}>{v || "(Blanks)"}</span>
            </label>
          ))}
          {!shown.length && <p className="px-1.5 py-1 text-slate-400">No matches</p>}
        </div>
        <div className="mt-2 flex items-center justify-between gap-2 border-t border-slate-100 pt-2 dark:border-slate-700">
          <button type="button" onClick={() => onApply(null)} disabled={!allowed}
            className="rounded px-2 py-1 text-rose-600 hover:bg-rose-50 disabled:opacity-40 dark:hover:bg-rose-500/10">Clear filter</button>
          <span className="flex gap-1.5">
            <button type="button" onClick={onClose} className="rounded border border-slate-200 px-2.5 py-1 hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700">Cancel</button>
            <button type="button" onClick={apply} disabled={!chosen.length} className="rounded bg-indigo-600 px-3 py-1 font-medium text-white hover:bg-indigo-500 disabled:opacity-40">OK</button>
          </span>
        </div>
      </div>
    </>
  );
}

const btn = "inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium shadow-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50";

export function ErpExcelEditor({
  jobId, readOnly: userReadOnly, onClose, onSaved, submit,
}: { jobId: string; readOnly: boolean; onClose: () => void; onSaved: () => void; submit?: ErpExcelSubmit | null }) {
  const [sheets, setSheets] = useState<jobsApi.ErpExcelSheet[] | null>(null);
  const [edited, setEdited] = useState<jobsApi.ErpExcelEdited | null>(null);
  const [file, setFile] = useState("");
  const [locked, setLocked] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<"save" | "reset" | "submit" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Every sheet of the workbook is shown by default - the ERP import reads them all.
  const [onlyFilled, setOnlyFilled] = useState(false);
  const [filters, setFilters] = useState<Record<number, ColFilters>>({});
  const [menuCol, setMenuCol] = useState<number | null>(null);
  // Rows edited or added while a filter is on stay visible, even if they no longer match -
  // a row must not vanish under the cursor mid-edit.
  const [touched, setTouched] = useState<Set<number>>(new Set());
  const [zoom, setZoom] = useState<number>(() => {
    try { const z = Number(localStorage.getItem(ZOOM_KEY)); return ZOOMS.includes(z) ? z : 1; } catch { return 1; }
  });
  const readOnly = userReadOnly || !!locked;

  useEffect(() => { try { localStorage.setItem(ZOOM_KEY, String(zoom)); } catch { /* not needed */ } }, [zoom]);

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
  const colFilters: ColFilters = filters[active] ?? {};
  const filterCount = Object.keys(colFilters).length;
  const keep = (r: number) => setTouched((t) => (filterCount && !t.has(r) ? new Set(t).add(r) : t));

  const setCell = (r: number, c: number, v: string) => {
    keep(r);
    update((rows) => {
      while (rows[r].length <= c) rows[r].push("");
      rows[r][c] = v;
      return rows;
    });
  };
  const addRow = () => {
    keep(sheet?.rows.length ?? 0);
    update((rows) => [...rows, Array(Math.max(width, 1)).fill("")]);
  };
  const delRow = (r: number) => {
    setTouched((t) => new Set([...t].filter((x) => x !== r).map((x) => (x > r ? x - 1 : x))));
    update((rows) => rows.filter((_, i) => i !== r));
  };

  function selectSheet(i: number) {
    setActive(i);
    setMenuCol(null);
    setTouched(new Set());
  }
  function setColFilter(c: number, allowed: string[] | null) {
    setFilters((f) => {
      const cur = { ...(f[active] ?? {}) };
      if (allowed === null) delete cur[c];
      else cur[c] = allowed;
      return { ...f, [active]: cur };
    });
    setTouched(new Set());
    setMenuCol(null);
  }
  function clearFilters() {
    setFilters((f) => ({ ...f, [active]: {} }));
    setTouched(new Set());
  }
  const distinct = (c: number) =>
    [...new Set((sheet?.rows ?? []).slice(1).map((row) => cellKey(row[c])))]
      .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  /** Data rows (by their real index) that pass this sheet's filters. */
  const bodyRows = useMemo(() => {
    const rows = sheet?.rows ?? [];
    const entries = Object.entries(filters[active] ?? {});
    const out: number[] = [];
    for (let r = 1; r < rows.length; r++) {
      if (touched.has(r) || entries.every(([c, allowed]) => allowed.includes(cellKey(rows[r][Number(c)])))) out.push(r);
    }
    return out;
  }, [sheet, filters, active, touched]);

  /** true when saved (or nothing to save). */
  async function save(): Promise<boolean> {
    if (!sheets || !dirty) return true;
    setBusy("save");
    setError(null);
    try {
      const d = await jobsApi.saveJobExcel(jobId, sheets);
      setEdited(d.edited);
      setDirty(false);
      onSaved();
      return true;
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not save." : "Could not save.");
      return false;
    } finally {
      setBusy(null);
    }
  }
  async function saveAndClose() {
    if (await save()) onClose();
  }
  async function reset() {
    if (!window.confirm("Throw away the edited Excel and build it again from the job's data?")) return;
    setBusy("reset");
    setError(null);
    try {
      await jobsApi.resetJobExcel(jobId);
      await load();
      onSaved();
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not reset." : "Could not reset.");
    } finally {
      setBusy(null);
    }
  }
  async function submitNow() {
    if (!submit || submit.disabled) return;
    if (!window.confirm(dirty ? `Save your changes and ${submit.label.toLowerCase()} now?` : `${submit.label} now?`)) return;
    if (!(await save())) return;
    setBusy("submit");
    onClose();
    submit.run();
  }
  function close() {
    if (dirty && !window.confirm("You have unsaved changes. Close without saving?")) return;
    onClose();
  }

  // Ctrl/Cmd+S saves, Esc closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); if (!readOnly) save(); }
      else if (e.key === "Escape") { if (menuCol !== null) setMenuCol(null); else close(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const zi = ZOOMS.indexOf(zoom);
  const nonEmpty = (s: jobsApi.ErpExcelSheet) => s.rows.slice(1).some((r) => r.some((c) => c.trim()));
  const tabs = (sheets ?? []).map((s, i) => ({ s, i })).filter(({ s, i }) => !onlyFilled || nonEmpty(s) || i === active);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-slate-50 dark:bg-slate-950">
      {/* Title bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-5 py-3 dark:border-slate-800 dark:bg-slate-900">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-300">
            <TableIcon className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <h3 className="flex items-center gap-2 truncate text-base font-semibold text-slate-900 dark:text-slate-50">
              ERP Excel <span className="font-normal text-slate-400">·</span> <span className="truncate">{file || "…"}</span>
              {dirty && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-500/20 dark:text-amber-200">Unsaved changes</span>}
              {!dirty && edited && <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-[11px] font-medium text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-200">Edited copy</span>}
            </h3>
            <p className="truncate text-xs text-slate-500">
              {edited
                ? `Saved${edited.saved_by ? ` by ${edited.saved_by}` : ""}${edited.saved_at ? ` on ${formatDateTime(edited.saved_at)}` : ""} — Download and Submit to ERP use this copy.`
                : "Built from the job's data. Saving makes your copy the one used for Download and Submit to ERP."}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!readOnly && edited && (
            <button type="button" onClick={reset} disabled={busy !== null} title="Throw away the edits and build the Excel from the job's data again"
              className={`${btn} border border-amber-200 bg-amber-50 text-amber-800 hover:bg-amber-100 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200 dark:hover:bg-amber-500/20`}>
              <ArrowPathIcon className="h-4 w-4" />
              {busy === "reset" ? "Resetting…" : "Reset to generated"}
            </button>
          )}
          <button type="button" onClick={close} title="Close (Esc)"
            className={`${btn} border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700`}>
            <XIcon className="h-4 w-4" />
            Close
          </button>
          {!readOnly && (
            <>
              <span className="mx-1 h-6 w-px bg-slate-200 dark:bg-slate-700" />
              <button type="button" onClick={save} disabled={busy !== null || !dirty} title="Save (Ctrl+S)"
                className={`${btn} border border-indigo-200 bg-white text-indigo-700 hover:bg-indigo-50 dark:border-indigo-500/40 dark:bg-slate-800 dark:text-indigo-200 dark:hover:bg-indigo-500/10`}>
                {dirty ? <SaveIcon className="h-4 w-4" /> : <CheckCircleIcon className="h-4 w-4" />}
                {busy === "save" ? "Saving…" : dirty ? "Save" : "Saved"}
              </button>
              <button type="button" onClick={saveAndClose} disabled={busy !== null}
                className={`${btn} bg-indigo-600 text-white shadow-indigo-600/20 hover:bg-indigo-500`}>
                <SaveIcon className="h-4 w-4" />
                Save &amp; Close
              </button>
            </>
          )}
          {!userReadOnly && submit && (
            <button type="button" onClick={submitNow} disabled={busy !== null || submit.disabled}
              title={submit.disabled ? submit.hint : dirty ? "Saves your changes first, then submits" : undefined}
              className={`${btn} bg-emerald-600 font-semibold text-white shadow-emerald-600/25 hover:bg-emerald-500 disabled:bg-slate-300 disabled:text-slate-500 disabled:opacity-100 disabled:shadow-none dark:disabled:bg-slate-700 dark:disabled:text-slate-400`}>
              <ArrowRightIcon className="h-4 w-4" />
              {busy === "submit" ? "Submitting…" : submit.label}
            </button>
          )}
        </div>
      </div>

      {locked && (
        <p className="border-b border-amber-200 bg-amber-50 px-5 py-2 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          {locked} You can still view and download it.
        </p>
      )}
      {!readOnly && submit?.disabled && submit.hint && (
        <p className="border-b border-sky-200 bg-sky-50 px-5 py-2 text-xs text-sky-800 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-200">
          {submit.hint}
        </p>
      )}
      {error && <p className="border-b border-rose-200 bg-rose-50 px-5 py-2 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}

      {/* Sheet tabs + view controls */}
      <div className="flex items-center justify-between gap-3 border-b border-slate-200 bg-white px-5 py-2 dark:border-slate-800 dark:bg-slate-900">
        <div className="flex min-w-0 gap-1 overflow-x-auto">
          {tabs.map(({ s, i }) => (
            <button key={s.name} type="button" onClick={() => selectSheet(i)} title={nonEmpty(s) ? undefined : "No data rows in this sheet"}
              className={`shrink-0 rounded-md px-3 py-1.5 text-xs font-medium ${i === active ? "bg-emerald-600 text-white shadow-sm" : nonEmpty(s) ? "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800" : "text-slate-400 hover:bg-slate-100 dark:text-slate-500 dark:hover:bg-slate-800"}`}>
              {s.name}
              {Object.keys(filters[i] ?? {}).length > 0 && <FunnelIcon className="ml-1 inline h-3 w-3" />}
            </button>
          ))}
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-slate-500">
            <input type="checkbox" checked={onlyFilled} onChange={(e) => setOnlyFilled(e.target.checked)} /> Only sheets with data
          </label>
          <div className="flex items-center rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800">
            <button type="button" onClick={() => setZoom(ZOOMS[Math.max(0, zi - 1)])} disabled={zi <= 0} title="Zoom out"
              className="p-1.5 text-slate-600 hover:bg-slate-100 disabled:opacity-40 dark:text-slate-300 dark:hover:bg-slate-700">
              <ZoomOutIcon className="h-4 w-4" />
            </button>
            <button type="button" onClick={() => setZoom(1)} title="Reset zoom"
              className="w-12 border-x border-slate-200 py-1 text-center text-xs font-medium text-slate-600 dark:border-slate-700 dark:text-slate-300">
              {Math.round(zoom * 100)}%
            </button>
            <button type="button" onClick={() => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, zi + 1)])} disabled={zi >= ZOOMS.length - 1} title="Zoom in"
              className="p-1.5 text-slate-600 hover:bg-slate-100 disabled:opacity-40 dark:text-slate-300 dark:hover:bg-slate-700">
              <ZoomInIcon className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {filterCount > 0 && sheet && (
        <div className="flex items-center gap-3 border-b border-indigo-200 bg-indigo-50 px-5 py-1.5 text-xs text-indigo-800 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-200">
          <FunnelIcon className="h-3.5 w-3.5" />
          Filter on {filterCount} column{filterCount > 1 ? "s" : ""} — showing {bodyRows.length} of {sheet.rows.length - 1} rows.
          Filters only change what you see; Save and Submit always use every row.
          <button type="button" onClick={clearFilters} className="ml-auto rounded px-2 py-0.5 font-medium hover:bg-indigo-100 dark:hover:bg-indigo-500/20">Clear filters</button>
        </div>
      )}

      {/* Grid */}
      <div className="min-h-0 flex-1 overflow-auto bg-white dark:bg-slate-900">
        {!sheets && !error && <p className="p-5 text-sm text-slate-500">Opening…</p>}
        {sheet && (
          <div style={{ zoom }}>
            <table className="border-separate border-spacing-0 text-xs">
              <thead>
                <tr>
                  <th className="sticky left-0 top-0 z-30 h-7 min-w-[3.25rem] border-b border-r border-slate-200 bg-slate-100 dark:border-slate-700 dark:bg-slate-800" />
                  {Array.from({ length: width }, (_, c) => (
                    <th key={c} className="sticky top-0 z-20 h-7 border-b border-r border-slate-200 bg-slate-100 px-2 py-1 font-medium text-slate-500 dark:border-slate-700 dark:bg-slate-800">{colName(c)}</th>
                  ))}
                </tr>
                {sheet.rows.length > 0 && (
                  <tr>
                    <th className="sticky left-0 top-7 z-30 min-w-[3.25rem] border-b border-r border-slate-200 bg-slate-50 px-1.5 text-right font-normal text-slate-400 shadow-[2px_0_0_0_rgba(148,163,184,0.25)] dark:border-slate-700 dark:bg-slate-800">1</th>
                    {Array.from({ length: width }, (_, c) => {
                      const head = sheet.rows[0][c] ?? "";
                      const on = !!colFilters[c];
                      return (
                        <th key={c} className={`sticky top-7 ${menuCol === c ? "z-40" : "z-20"} border-b border-r border-slate-200 bg-slate-50 p-0 text-left dark:border-slate-700 dark:bg-slate-800`}>
                          <div className="relative flex items-center">
                            <span title={`${head} (header - locked)`} className="w-32 truncate px-1.5 py-1 font-semibold text-slate-800 dark:text-slate-100">{head}</span>
                            <button type="button" onClick={() => setMenuCol(menuCol === c ? null : c)} title={on ? "Filtered - change or clear" : "Filter this column"}
                              className={`mr-1 rounded p-0.5 ${on ? "bg-indigo-600 text-white" : "text-slate-400 hover:bg-slate-200 hover:text-slate-700 dark:hover:bg-slate-700 dark:hover:text-slate-200"}`}>
                              <FunnelIcon className="h-3.5 w-3.5" />
                            </button>
                            {menuCol === c && (
                              <FilterMenu values={distinct(c)} allowed={colFilters[c]} onApply={(a) => setColFilter(c, a)} onClose={() => setMenuCol(null)} />
                            )}
                          </div>
                        </th>
                      );
                    })}
                  </tr>
                )}
              </thead>
              <tbody>
                {bodyRows.map((r) => { const row = sheet.rows[r]; return (
                  <tr key={r} className="group">
                    <td className="sticky left-0 z-10 min-w-[3.25rem] border-b border-r border-slate-200 bg-slate-50 px-1.5 text-right text-slate-400 shadow-[2px_0_0_0_rgba(148,163,184,0.25)] dark:border-slate-700 dark:bg-slate-800">
                      <span className="inline-flex items-center gap-1">
                        {!readOnly && (
                          <button type="button" onClick={() => delRow(r)} title="Delete this row"
                            className="invisible text-rose-500 hover:text-rose-700 group-hover:visible">
                            <XIcon className="h-3 w-3" />
                          </button>
                        )}
                        {r + 1}
                      </span>
                    </td>
                    {Array.from({ length: width }, (_, c) => (
                      <td key={c} className="border-b border-r border-slate-200 p-0 dark:border-slate-700">
                        <input
                          value={row[c] ?? ""}
                          readOnly={readOnly}
                          onChange={(e) => setCell(r, c, e.target.value)}
                          title={row[c] ?? ""}
                          className="w-[9.5rem] bg-transparent px-1.5 py-1 text-slate-800 outline-none focus:bg-indigo-50 focus:ring-2 focus:ring-inset focus:ring-indigo-400 dark:text-slate-100 dark:focus:bg-indigo-500/10"
                        />
                      </td>
                    ))}
                  </tr>
                ); })}
                {filterCount > 0 && bodyRows.length === 0 && (
                  <tr><td colSpan={width + 1} className="px-3 py-4 text-slate-500">No rows match the filter.</td></tr>
                )}
              </tbody>
            </table>
            {!readOnly && (
              <button type="button" onClick={addRow}
                className="m-3 inline-flex items-center gap-1 rounded-md border border-dashed border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700 dark:border-slate-600 dark:text-slate-300">
                + Add row
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
