import {
  memo, useCallback, useEffect, useMemo, useRef, useState,
  type ClipboardEvent as ReactClipboardEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode, type SVGProps,
} from "react";
import axios from "axios";
import * as jobsApi from "../../api/jobs";
import { formatDateTime } from "../../utils/dateTime";
import { ArrowPathIcon, ArrowRightIcon, CheckCircleIcon } from "../../components/ui/icons";

/** The job's ERP-import Excel, opened and edited inside the application: every sheet as a grid,
 *  cells editable (the header row is locked - the ERP import maps columns by it), rows can be
 *  added or removed. Save keeps it as the job's workbook - from then on Download ERP Excel and
 *  the real ERP import use it, until "Reset to generated". Submit runs the page's own Submit
 *  (same checks), saving first.
 *
 *  It behaves like a spreadsheet, because the edits people actually make here are spreadsheet
 *  edits - the same country code down forty item lines, a column pasted across from their own
 *  workbook, a serial number continued:
 *
 *    * the grid does not stop at the last filled row. Blank rows are always drawn under it and
 *      typing in one turns it into a real row - a sheet holding one invoice line looked broken.
 *    * one cell is active; click and drag, shift-click or the arrow keys select a range, and
 *      the column letter or the row number takes the whole column or row.
 *    * the blue square at the bottom-right of the selection drags to fill: values repeat, and
 *      a run of whole numbers continues (1, 2 -> 3, 4, 5 - how ItemSrNo gets filled).
 *    * Ctrl+C / Ctrl+X / Ctrl+V move a block as tab-separated text, so copy and paste work
 *      against real Excel in both directions, and one copied cell fills a whole selection.
 *    * Ctrl+Z / Ctrl+Y undo and redo, because one drag can rewrite a hundred cells.
 *
 *  Two limits belong to the workbook, not the screen, and the grid shows them rather than let
 *  someone type into nothing: the header row is never written (the ERP import maps columns by
 *  it), and a sheet has only the columns its headers define - the greyed columns on the right
 *  draw the grid out to the edge and cannot be typed into, because save_sheets() writes only as
 *  far as the sheet's own last column and would silently drop anything past it.
 *
 *  ITEMS reaches 145 rows by 127 columns on a real job, so a row is a memo()'d component:
 *  dragging a selection across the sheet re-renders the rows the selection actually crossed,
 *  not twenty thousand cells per mouse move. */

export interface ErpExcelSubmit {
  label: string;
  disabled: boolean;
  /** Why it can't be used right now, shown as the tooltip. */
  hint?: string;
  run: () => void;
}

const ZOOMS = [0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.25, 1.5];
const ZOOM_KEY = "erpExcelEditor.zoom";

/** Blank rows kept under the last filled one, so the sheet looks like a sheet and there is
 *  somewhere to type. They become real rows only when something is typed into them. */
const PAD_ROWS = 40;
/** Greyed, uneditable columns drawn past the sheet's last column - the grid to the edge. */
const PAD_COLS = 4;
/** app/core/erp_excel_edit.py saves at most this many rows, so the grid offers no more. */
const MAX_ROWS = 2000;
const DEFAULT_COL_W = 152;
const GUTTER_W = 56;
// Row height is written out as h-[26px] in the cell classes - Tailwind cannot interpolate it.
const UNDO_DEPTH = 40;

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
const UndoIcon = (p: SVGProps<SVGSVGElement>) => svg(p, <><path d="M9 14L4 9l5-5" /><path d="M4 9h10a6 6 0 0 1 0 12H7" /></>);
const RedoIcon = (p: SVGProps<SVGSVGElement>) => svg(p, <><path d="M15 14l5-5-5-5" /><path d="M20 9H10a6 6 0 0 0 0 12h7" /></>);

/** Per sheet: column -> the values still shown (a column without an entry is not filtered). */
type ColFilters = Record<number, string[]>;

const cellKey = (v: string | undefined) => (v ?? "").trim();

// ---------------------------------------------------------------------------------------
// Selection. Rows are held as VIEW positions, not row numbers: with a column filter on, the
// rows between two visible ones are hidden, and arrowing down, filling and pasting must step
// over them the way Excel does. `view[v]` turns a position back into the sheet's own row.
// ---------------------------------------------------------------------------------------
type Pos = { v: number; c: number };
type Rect = { v0: number; v1: number; c0: number; c1: number };
type Sel = { a: Pos; h: Pos };

const rectOf = (s: Sel): Rect => ({
  v0: Math.min(s.a.v, s.h.v), v1: Math.max(s.a.v, s.h.v),
  c0: Math.min(s.a.c, s.h.c), c1: Math.max(s.a.c, s.h.c),
});
const inRect = (r: Rect, v: number, c: number) => v >= r.v0 && v <= r.v1 && c >= r.c0 && c <= r.c1;
/** This row's slice of a rectangle, as a pair of columns - all a row needs to know of it, and
 *  primitives, so that memo() can compare it. [-1, -1] when the rectangle misses the row. */
const spanIn = (r: Rect | null, v: number): [number, number] =>
  r && v >= r.v0 && v <= r.v1 ? [r.c0, r.c1] : [-1, -1];

// ---- the clipboard, as the tab-separated text Excel itself puts there ----
const quote = (s: string) => (/[\t\n"]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
const toTsv = (block: string[][]) => block.map((r) => r.map(quote).join("\t")).join("\n");

/** Tab-separated text back into a block. Excel quotes any cell holding a tab, a newline or a
 *  quote, so a pasted address does not become four columns. */
function fromTsv(text: string): string[][] {
  const t = text.replace(/\r\n?/g, "\n");
  const out: string[][] = [[]];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < t.length; i++) {
    const ch = t[i];
    if (quoted) {
      if (ch !== '"') cell += ch;
      else if (t[i + 1] === '"') { cell += '"'; i++; }
      else quoted = false;
    } else if (ch === '"' && cell === "") quoted = true;
    else if (ch === "\t") { out[out.length - 1].push(cell); cell = ""; }
    else if (ch === "\n") { out[out.length - 1].push(cell); cell = ""; out.push([]); }
    else cell += ch;
  }
  out[out.length - 1].push(cell);
  // the newline Excel puts after the last row is not a row
  if (out.length > 1 && out[out.length - 1].length === 1 && out[out.length - 1][0] === "") out.pop();
  return out;
}

const WHOLE_NUMBER = /^-?\d+$/;

/** The next `n` values after (dir 1) or before (dir -1) a run, when the run is whole numbers
 *  moving in equal steps - 1, 2 continues 3, 4, 5. Anything else returns null and the caller
 *  repeats the values instead, which is what Excel does too. */
function series(src: string[], n: number, dir: 1 | -1): string[] | null {
  const parts = src.map((s) => s.trim());
  if (parts.length < 2 || !parts.every((s) => WHOLE_NUMBER.test(s))) return null;
  const nums = parts.map((s) => parseInt(s, 10));
  const step = nums[1] - nums[0];
  if (step === 0) return null;
  for (let i = 2; i < nums.length; i++) if (nums[i] - nums[i - 1] !== step) return null;
  const out: string[] = [];
  let cur = dir === 1 ? nums[nums.length - 1] : nums[0];
  for (let i = 0; i < n; i++) { cur += dir === 1 ? step : -step; out.push(String(cur)); }
  return out;  // for dir -1 the first entry is the one nearest the run
}

const isNumberLike = (s: string) => s !== "" && /^-?[\d,]*\.?\d+$/.test(s.trim());

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

// ---------------------------------------------------------------------------------------
// One row of the grid. Everything it is given is a primitive or an array that changes only
// when the sheet does, so dragging a selection re-renders the rows whose highlight actually
// changed. Mouse handling is delegated to the <tbody>, hence no per-cell callbacks.
// ---------------------------------------------------------------------------------------
type RowProps = {
  v: number; r: number; cells: string[] | undefined; colCount: number;
  beyond: boolean; readOnly: boolean; rowSelected: boolean; padTitle: string;
  selC0: number; selC1: number;
  fillC0: number; fillC1: number;
  copC0: number; copC1: number;
  activeC: number; handleC: number;
  editC: number; editValue: string; editInCell: boolean;
  onEditChange: (value: string) => void;
  onEditBlur: () => void;
  onEditKey: (e: ReactKeyboardEvent<HTMLInputElement>) => void;
};

const GridRow = memo(function GridRow({
  v, r, cells, colCount, beyond, readOnly, rowSelected, padTitle,
  selC0, selC1, fillC0, fillC1, copC0, copC1, activeC, handleC,
  editC, editValue, editInCell, onEditChange, onEditBlur, onEditKey,
}: RowProps) {
  return (
    <tr>
      <td data-gutter={v} title={beyond ? "A blank row — type in it and it becomes part of the sheet" : "Click to select this row"}
        className={`group sticky left-0 z-10 h-[26px] cursor-pointer border-b border-r border-slate-200 px-1.5 text-right shadow-[2px_0_0_0_rgba(148,163,184,0.25)] dark:border-slate-700 ${
          rowSelected ? "bg-indigo-100 text-indigo-800 dark:bg-indigo-500/25 dark:text-indigo-100"
            : beyond ? "bg-slate-50 text-slate-300 dark:bg-slate-800 dark:text-slate-600"
              : "bg-slate-50 text-slate-400 dark:bg-slate-800"}`}>
        <span className="inline-flex items-center gap-1">
          {!readOnly && !beyond && (
            <button type="button" data-del="1" title="Delete this row"
              className="invisible text-rose-500 hover:text-rose-700 group-hover:visible">
              <XIcon className="h-3 w-3" />
            </button>
          )}
          {r + 1}
        </span>
      </td>
      {Array.from({ length: colCount }, (_, c) => {
        const text = cells?.[c] ?? "";
        const isActive = c === activeC;
        const inSel = c >= selC0 && c <= selC1;
        const inFill = c >= fillC0 && c <= fillC1 && !inSel;
        const isCopied = c >= copC0 && c <= copC1;
        const editing = c === editC;
        return (
          <td
            key={c}
            data-v={v}
            data-c={c}
            className={`relative h-[26px] border-b border-r border-slate-200 p-0 dark:border-slate-700 ${
              inFill ? "bg-indigo-100/70 dark:bg-indigo-500/20"
                : inSel && !isActive ? "bg-indigo-50 dark:bg-indigo-500/10"
                  : ""
            } ${isCopied ? "outline-1 outline-dashed -outline-offset-1 outline-indigo-400" : ""}`}
            style={isActive && !editing ? { boxShadow: "inset 0 0 0 2px rgb(79 70 229)" } : undefined}
          >
            {editing && editInCell ? (
              <input autoFocus value={editValue}
                onChange={(e) => onEditChange(e.target.value)}
                onBlur={onEditBlur}
                onKeyDown={onEditKey}
                onMouseDown={(e) => e.stopPropagation()}
                className="h-[26px] w-full bg-white px-1.5 text-slate-900 outline-none ring-2 ring-inset ring-indigo-600 dark:bg-slate-900 dark:text-slate-50" />
            ) : (
              <div title={text || undefined}
                className={`h-[26px] overflow-hidden whitespace-nowrap px-1.5 leading-[26px] text-slate-800 dark:text-slate-100 ${isNumberLike(text) ? "text-right" : ""}`}>
                {editing ? editValue : text}
              </div>
            )}
            {c === handleC && !editing && (
              <span data-handle="1" title="Drag to fill down or across"
                className="absolute -bottom-[3px] -right-[3px] z-20 h-[7px] w-[7px] cursor-crosshair bg-indigo-600 ring-1 ring-white dark:ring-slate-900" />
            )}
          </td>
        );
      })}
      {Array.from({ length: PAD_COLS }, (_, i) => (
        <td key={`pad${i}`} title={padTitle}
          className="h-[26px] border-b border-r border-slate-200 bg-slate-50/40 p-0 dark:border-slate-700 dark:bg-slate-800/30" />
      ))}
    </tr>
  );
});

const btn = "inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium shadow-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50";

/** One undone step: the sheet it belongs to and that sheet's rows as they were. `update` builds
 *  new arrays rather than changing the old ones, so keeping the old reference is enough. */
type Snapshot = { sheet: number; rows: string[][] };

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

  /** The workbook and the unsaved flag, mirrored, so that an edit still sitting in a cell is
   *  carried by Save. Clicking Save blurs the cell, which commits it, but the click handler's
   *  own `sheets` and `dirty` were captured one render earlier and would not have it. */
  const sheetsRef = useRef<jobsApi.ErpExcelSheet[] | null>(null);
  const dirtyRef = useRef(false);

  // ---- spreadsheet state ----
  const [sel, setSel] = useState<Sel | null>(null);
  const [editAt, setEditAt] = useState<Pos | null>(null);
  const [editValue, setEditValue] = useState("");
  /** The live edit, in a ref as well, so committing is final the instant it happens: Enter
   *  commits and moves on, which blurs the input, and the blur must not commit a second time. */
  const editRef = useRef<{ pos: Pos; value: string; inBar: boolean } | null>(null);
  const [fillTo, setFillTo] = useState<Pos | null>(null);
  const [copied, setCopied] = useState<Rect | null>(null);
  const [past, setPast] = useState<Snapshot[]>([]);
  const [future, setFuture] = useState<Snapshot[]>([]);
  const [colW, setColW] = useState<Record<number, Record<number, number>>>({});
  const [ctx, setCtx] = useState<{ x: number; y: number; v: number; c: number } | null>(null);
  const drag = useRef<"select" | "fill" | null>(null);
  const gridRef = useRef<HTMLDivElement | null>(null);
  /** Where to scroll next, set only when the keyboard moved the selection. */
  const scrollTo = useRef<Pos | null>(null);

  const readOnly = userReadOnly || !!locked;

  useEffect(() => { try { localStorage.setItem(ZOOM_KEY, String(zoom)); } catch { /* not needed */ } }, [zoom]);

  async function load() {
    setError(null);
    try {
      const d = await jobsApi.openJobExcel(jobId);
      sheetsRef.current = d.sheets;
      dirtyRef.current = false;
      editRef.current = null;
      setSheets(d.sheets);
      setEdited(d.edited);
      setFile(d.file);
      setLocked(d.locked ?? null);
      setDirty(false);
      setPast([]);
      setFuture([]);
      setSel(null);
      setCopied(null);
      setEditAt(null);
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not open the Excel." : "Could not open the Excel.");
    }
  }
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [jobId]);

  const sheet = sheets?.[active];
  /** The sheet's own columns. Nothing past this is saved - save_sheets() writes only as far as
   *  the workbook's last column - so nothing past this is editable either. */
  const colCount = useMemo(() => Math.max(0, ...(sheet?.rows ?? []).map((r) => r.length)), [sheet]);

  /** Every change to the workbook goes through here: one undo step, mirror kept in step. */
  function update(fn: (rows: string[][]) => string[][]) {
    const cur = sheetsRef.current;
    if (!cur) return;
    setPast((p) => [...p.slice(-(UNDO_DEPTH - 1)), { sheet: active, rows: cur[active].rows }]);
    setFuture([]);
    const next = cur.map((s, i) => (i === active ? { ...s, rows: fn(s.rows.map((r) => [...r])) } : s));
    sheetsRef.current = next;
    dirtyRef.current = true;
    setSheets(next);
    setDirty(true);
  }
  const colFilters: ColFilters = filters[active] ?? {};
  const filterCount = Object.keys(colFilters).length;
  const keep = (r: number) => setTouched((t) => (filterCount && !t.has(r) ? new Set(t).add(r) : t));

  /** Write a batch of cells as ONE undo step - a fill or a paste is one action, not a hundred.
   *  A row past the end of the sheet becomes a real row only when something is typed into it,
   *  so clicking into the blank rows and pressing Delete does not grow the workbook. */
  function setCells(list: { r: number; c: number; v: string }[]) {
    if (readOnly || !list.length) return;
    update((rows) => {
      for (const { r, c, v } of list) {
        if (r === 0 || r >= MAX_ROWS || c < 0 || c >= colCount) continue;  // header row, and the sheet's own width
        if (r >= rows.length) {
          if (!v.trim()) continue;
          while (rows.length <= r) rows.push([]);
        }
        const row = rows[r];
        while (row.length <= c) row.push("");
        row[c] = v;
      }
      return rows;
    });
    const rowsNow = sheet?.rows.length ?? 0;
    for (const e of list) if (e.r < rowsNow) keep(e.r);
  }
  const setCell = (r: number, c: number, v: string) => setCells([{ r, c, v }]);

  const addRow = () => {
    keep(sheet?.rows.length ?? 0);
    update((rows) => [...rows, Array(Math.max(colCount, 1)).fill("")]);
  };
  const delRow = (r: number) => {
    setTouched((t) => new Set([...t].filter((x) => x !== r).map((x) => (x > r ? x - 1 : x))));
    update((rows) => rows.filter((_, i) => i !== r));
  };
  const insertRow = (at: number) => {
    if ((sheet?.rows.length ?? 0) >= MAX_ROWS) return;
    setTouched((t) => new Set([...t].map((x) => (x >= at ? x + 1 : x))));
    update((rows) => [...rows.slice(0, at), Array(Math.max(colCount, 1)).fill(""), ...rows.slice(at)]);
  };

  function undo() {
    const cur = sheetsRef.current;
    if (!past.length || !cur) return;
    const step = past[past.length - 1];
    setFuture((f) => [...f, { sheet: step.sheet, rows: cur[step.sheet].rows }]);
    const next = cur.map((s, i) => (i === step.sheet ? { ...s, rows: step.rows } : s));
    sheetsRef.current = next;
    dirtyRef.current = true;
    setSheets(next);
    setPast(past.slice(0, -1));
    setActive(step.sheet);
    setDirty(true);
  }
  function redo() {
    const cur = sheetsRef.current;
    if (!future.length || !cur) return;
    const step = future[future.length - 1];
    setPast((p) => [...p.slice(-(UNDO_DEPTH - 1)), { sheet: step.sheet, rows: cur[step.sheet].rows }]);
    const next = cur.map((s, i) => (i === step.sheet ? { ...s, rows: step.rows } : s));
    sheetsRef.current = next;
    dirtyRef.current = true;
    setSheets(next);
    setFuture(future.slice(0, -1));
    setActive(step.sheet);
    setDirty(true);
  }

  function selectSheet(i: number) {
    cancelEdit();
    setActive(i);
    setMenuCol(null);
    setTouched(new Set());
    setSel(null);
    setCopied(null);
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
    setSel(null);
  }
  function clearFilters() {
    setFilters((f) => ({ ...f, [active]: {} }));
    setTouched(new Set());
    setSel(null);
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

  /** Every row the grid draws, in the order it draws them: the rows that pass the filter, then
   *  the blank ones under the sheet. A position in here is what the selection holds. */
  const padStart = Math.max(1, sheet?.rows.length ?? 1);
  const padCount = Math.max(0, Math.min(PAD_ROWS, MAX_ROWS - padStart));
  const view = useMemo(
    () => [...bodyRows, ...Array.from({ length: padCount }, (_, i) => padStart + i)],
    [bodyRows, padStart, padCount],
  );
  /** A position below the blank rows - only a paste long enough to need them gets there. */
  const realRow = (v: number) => (v < view.length ? view[v] : padStart + padCount + (v - view.length));
  const textAt = (v: number, c: number) => sheet?.rows[realRow(v)]?.[c] ?? "";

  const rect = sel ? rectOf(sel) : null;
  const activeText = sel ? textAt(sel.a.v, sel.a.c) : "";
  const canEditCell = !readOnly && colCount > 0;

  // ---- moving about ----
  function goTo(p: Pos, extend = false) {
    if (!view.length || colCount === 0) return;
    const v = Math.max(0, Math.min(view.length - 1, p.v));
    const c = Math.max(0, Math.min(colCount - 1, p.c));
    scrollTo.current = { v, c };
    setSel((s) => (extend && s ? { a: s.a, h: { v, c } } : { a: { v, c }, h: { v, c } }));
  }
  const move = (dv: number, dc: number, extend: boolean) => {
    if (!sel) { goTo({ v: 0, c: 0 }); return; }
    const from = extend ? sel.h : sel.a;
    goTo({ v: from.v + dv, c: from.c + dc }, extend);
  };
  useEffect(() => {
    const p = scrollTo.current;
    if (!p) return;
    scrollTo.current = null;
    gridRef.current?.querySelector(`td[data-v="${p.v}"][data-c="${p.c}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  });

  // ---- editing one cell ----
  function startEdit(pos: Pos, value: string, inBar = false) {
    if (!canEditCell) return;
    editRef.current = { pos, value, inBar };
    setEditAt(pos);
    setEditValue(value);
  }
  function commitEdit() {
    const e = editRef.current;
    if (!e) return;
    editRef.current = null;
    setEditAt(null);
    if (e.value !== textAt(e.pos.v, e.pos.c)) setCell(realRow(e.pos.v), e.pos.c, e.value);
  }
  function cancelEdit() {
    editRef.current = null;
    setEditAt(null);
  }
  const focusGrid = () => gridRef.current?.focus();

  // The row is memo()'d, so the handlers it is given must keep the same identity from render
  // to render. They call through a ref that every render refreshes.
  const live = useRef({
    change: (_v: string) => { /* replaced below on every render */ },
    blur: () => { /* replaced below on every render */ },
    key: (_e: ReactKeyboardEvent<HTMLInputElement>) => { /* replaced below on every render */ },
  });
  live.current.change = (v: string) => { if (editRef.current) editRef.current.value = v; setEditValue(v); };
  live.current.blur = () => commitEdit();
  live.current.key = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") { e.preventDefault(); commitEdit(); move(e.shiftKey ? -1 : 1, 0, false); focusGrid(); }
    else if (e.key === "Tab") { e.preventDefault(); commitEdit(); move(0, e.shiftKey ? -1 : 1, false); focusGrid(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancelEdit(); focusGrid(); }
  };
  const onEditChange = useCallback((v: string) => live.current.change(v), []);
  const onEditBlur = useCallback(() => live.current.blur(), []);
  const onEditKey = useCallback((e: ReactKeyboardEvent<HTMLInputElement>) => live.current.key(e), []);

  // ---- the block under the selection ----
  function block(r: Rect): string[][] {
    const out: string[][] = [];
    for (let v = r.v0; v <= r.v1; v++) {
      const line: string[] = [];
      for (let c = r.c0; c <= r.c1; c++) line.push(textAt(v, c));
      out.push(line);
    }
    return out;
  }
  function clearRange() {
    if (!rect) return;
    const edits: { r: number; c: number; v: string }[] = [];
    for (let v = rect.v0; v <= rect.v1; v++) for (let c = rect.c0; c <= rect.c1; c++) edits.push({ r: realRow(v), c, v: "" });
    setCells(edits);
  }

  // ---- fill: the blue square, and Ctrl+D / Ctrl+R ----
  /** The rectangle a fill drag covers: it grows in ONE direction, whichever the pointer moved
   *  further in, exactly as the handle behaves in Excel. */
  function fillRect(from: Rect, to: Pos): Rect | null {
    const dv = to.v < from.v0 ? from.v0 - to.v : to.v > from.v1 ? to.v - from.v1 : 0;
    const dc = to.c < from.c0 ? from.c0 - to.c : to.c > from.c1 ? to.c - from.c1 : 0;
    if (!dv && !dc) return null;
    return dv >= dc
      ? { ...from, v0: Math.min(from.v0, to.v), v1: Math.max(from.v1, to.v) }
      : { ...from, c0: Math.min(from.c0, to.c), c1: Math.max(from.c1, to.c) };
  }
  function applyFill(from: Rect, target: Rect) {
    const edits: { r: number; c: number; v: string }[] = [];
    const down = target.v1 > from.v1;
    const up = target.v0 < from.v0;
    if (down || up) {
      for (let c = from.c0; c <= from.c1; c++) {
        const src: string[] = [];
        for (let v = from.v0; v <= from.v1; v++) src.push(textAt(v, c));
        if (down) {
          const n = target.v1 - from.v1;
          const run = series(src, n, 1);
          for (let i = 0; i < n; i++) {
            const v = from.v1 + 1 + i;
            edits.push({ r: realRow(v), c, v: run ? run[i] : src[(v - from.v0) % src.length] });
          }
        }
        if (up) {
          const n = from.v0 - target.v0;
          const run = series(src, n, -1);
          for (let i = 0; i < n; i++) {
            const v = from.v0 - 1 - i;
            edits.push({ r: realRow(v), c, v: run ? run[i] : src[(src.length - ((i + 1) % src.length)) % src.length] });
          }
        }
      }
    } else {
      for (let v = from.v0; v <= from.v1; v++) {
        const src: string[] = [];
        for (let c = from.c0; c <= from.c1; c++) src.push(textAt(v, c));
        if (target.c1 > from.c1) {
          const n = target.c1 - from.c1;
          const run = series(src, n, 1);
          for (let i = 0; i < n; i++) {
            const c = from.c1 + 1 + i;
            edits.push({ r: realRow(v), c, v: run ? run[i] : src[(c - from.c0) % src.length] });
          }
        }
        if (target.c0 < from.c0) {
          const n = from.c0 - target.c0;
          const run = series(src, n, -1);
          for (let i = 0; i < n; i++) {
            const c = from.c0 - 1 - i;
            edits.push({ r: realRow(v), c, v: run ? run[i] : src[(src.length - ((i + 1) % src.length)) % src.length] });
          }
        }
      }
    }
    setCells(edits);
    setSel({ a: { v: target.v0, c: target.c0 }, h: { v: target.v1, c: target.c1 } });
  }
  /** Ctrl+D / Ctrl+R: the first row (or column) of the selection over the rest of it. */
  function fillFromEdge(axis: "down" | "right") {
    if (!rect) return;
    if (axis === "down" ? rect.v1 <= rect.v0 : rect.c1 <= rect.c0) return;
    applyFill(axis === "down" ? { ...rect, v1: rect.v0 } : { ...rect, c1: rect.c0 }, rect);
  }

  // ---- clipboard ----
  function onCopy(e: ReactClipboardEvent) {
    if (editRef.current || !rect) return;
    e.preventDefault();
    e.clipboardData.setData("text/plain", toTsv(block(rect)));
    setCopied(rect);
  }
  function onCut(e: ReactClipboardEvent) {
    if (editRef.current || !rect) return;
    onCopy(e);
    if (!readOnly) clearRange();
  }
  function onPaste(e: ReactClipboardEvent) {
    if (editRef.current || !rect || readOnly) return;
    e.preventDefault();
    const grid = fromTsv(e.clipboardData.getData("text/plain"));
    if (!grid.length) return;
    // One cell pasted over a selection fills the whole selection - how one country code
    // reaches forty item lines in a single go.
    const single = grid.length === 1 && grid[0].length === 1;
    const rowsN = single ? rect.v1 - rect.v0 + 1 : grid.length;
    const colsN = single ? rect.c1 - rect.c0 + 1 : Math.max(...grid.map((r) => r.length));
    const edits: { r: number; c: number; v: string }[] = [];
    let lastV = rect.v0;
    for (let i = 0; i < rowsN; i++) {
      const r = realRow(rect.v0 + i);
      if (r >= MAX_ROWS) break;
      lastV = rect.v0 + i;
      for (let j = 0; j < colsN; j++) {
        const c = rect.c0 + j;
        if (c >= colCount) break;   // the sheet has no column there, and saving would drop it
        edits.push({ r, c, v: single ? grid[0][0] : grid[i]?.[j] ?? "" });
      }
    }
    setCells(edits);
    setSel({ a: { v: rect.v0, c: rect.c0 }, h: { v: lastV, c: Math.min(colCount - 1, rect.c0 + colsN - 1) } });
  }

  // ---- mouse on the grid (delegated from <tbody>) ----
  const posOf = (target: EventTarget | null): Pos | null => {
    const td = (target as HTMLElement | null)?.closest?.("td[data-v]") as HTMLElement | null;
    return td ? { v: Number(td.dataset.v), c: Number(td.dataset.c) } : null;
  };
  function onBodyMouseDown(e: ReactMouseEvent) {
    if (e.button === 2) return;             // the context-menu handler deals with the right button
    const el = e.target as HTMLElement;
    const gutter = el.closest?.("td[data-gutter]") as HTMLElement | null;
    if (gutter) {
      commitEdit();
      setCtx(null);
      if (!el.closest?.("[data-del]")) { setCopied(null); selectRow(Number(gutter.dataset.gutter)); }
      return;
    }
    const p = posOf(e.target);
    if (!p) return;
    commitEdit();
    setCtx(null);
    if (el.dataset.handle) { drag.current = "fill"; setFillTo(p); return; }
    drag.current = "select";
    setCopied(null);
    if (e.shiftKey && sel) setSel({ a: sel.a, h: p });
    else setSel({ a: p, h: p });
  }
  function onBodyClick(e: ReactMouseEvent) {
    const el = e.target as HTMLElement;
    const gutter = el.closest?.("td[data-gutter]") as HTMLElement | null;
    if (gutter && el.closest?.("[data-del]")) delRow(realRow(Number(gutter.dataset.gutter)));
  }
  function onBodyMouseOver(e: ReactMouseEvent) {
    if (!drag.current) return;
    const p = posOf(e.target);
    if (!p) return;
    // The pointer crosses the cell AND the text inside it, so the same position arrives twice -
    // and on ITEMS one needless re-render is twenty thousand cells.
    if (drag.current === "fill") setFillTo((f) => (f && f.v === p.v && f.c === p.c ? f : p));
    else setSel((s) => (!s || (s.h.v === p.v && s.h.c === p.c) ? s : { a: s.a, h: p }));
  }
  function onBodyDoubleClick(e: ReactMouseEvent) {
    const p = posOf(e.target);
    if (p) startEdit(p, textAt(p.v, p.c));
  }
  function onBodyContextMenu(e: ReactMouseEvent) {
    const el = e.target as HTMLElement;
    const gutter = el.closest?.("td[data-gutter]") as HTMLElement | null;
    const p = gutter ? { v: Number(gutter.dataset.gutter), c: 0 } : posOf(e.target);
    if (!p) return;
    e.preventDefault();
    if (!rect || !inRect(rect, p.v, p.c)) setSel({ a: p, h: p });
    setCtx({ x: e.clientX, y: e.clientY, v: p.v, c: p.c });
  }
  useEffect(() => {
    const up = () => {
      if (drag.current === "fill" && rect && fillTo) {
        const t = fillRect(rect, fillTo);
        if (t) applyFill(rect, t);
      }
      drag.current = null;
      setFillTo((f) => (f ? null : f));
    };
    window.addEventListener("mouseup", up);
    return () => window.removeEventListener("mouseup", up);
  });

  // ---- column widths ----
  const widths = colW[active] ?? {};
  const widthOf = (c: number) => widths[c] ?? DEFAULT_COL_W;
  const setWidth = (c: number, px: number) =>
    setColW((w) => ({ ...w, [active]: { ...(w[active] ?? {}), [c]: Math.max(44, Math.min(640, Math.round(px))) } }));
  function startResize(e: ReactMouseEvent, c: number) {
    e.preventDefault();
    e.stopPropagation();
    const x0 = e.clientX;
    const w0 = widthOf(c);
    const onMove = (ev: MouseEvent) => setWidth(c, w0 + (ev.clientX - x0) / zoom);  // the grid is zoomed, the pointer is not
    const onUp = () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }
  /** Double-click the edge: wide enough for the longest value in the column. */
  function autoFit(c: number) {
    let longest = (sheet?.rows[0]?.[c] ?? "").length;
    for (const v of view) longest = Math.max(longest, (sheet?.rows[v]?.[c] ?? "").length);
    setWidth(c, longest * 6.6 + 26);
  }

  const selectColumn = (c: number) => { setCopied(null); setSel({ a: { v: 0, c }, h: { v: Math.max(0, view.length - 1), c } }); };
  const selectRow = (v: number) => setSel({ a: { v, c: 0 }, h: { v, c: Math.max(0, colCount - 1) } });
  const selectAll = () => setSel({ a: { v: 0, c: 0 }, h: { v: Math.max(0, view.length - 1), c: Math.max(0, colCount - 1) } });

  // ---- keys on the grid (the cell editor has its own handler) ----
  function onGridKey(e: ReactKeyboardEvent) {
    if (editRef.current || menuCol !== null || ctx) return;
    const k = e.key;
    if (e.ctrlKey || e.metaKey) {
      const low = k.toLowerCase();
      if (low === "z") { e.preventDefault(); if (e.shiftKey) redo(); else undo(); }
      else if (low === "y") { e.preventDefault(); redo(); }
      else if (low === "a") { e.preventDefault(); selectAll(); }
      else if (low === "d") { e.preventDefault(); fillFromEdge("down"); }
      else if (low === "r") { e.preventDefault(); fillFromEdge("right"); }
      else if (k === "Home") { e.preventDefault(); goTo({ v: 0, c: 0 }, e.shiftKey); }
      else if (k === "End") { e.preventDefault(); goTo({ v: Math.max(0, bodyRows.length - 1), c: colCount - 1 }, e.shiftKey); }
      return;   // Ctrl+C / X / V arrive as copy / cut / paste events
    }
    switch (k) {
      case "ArrowUp": e.preventDefault(); move(-1, 0, e.shiftKey); break;
      case "ArrowDown": e.preventDefault(); move(1, 0, e.shiftKey); break;
      case "ArrowLeft": e.preventDefault(); move(0, -1, e.shiftKey); break;
      case "ArrowRight": e.preventDefault(); move(0, 1, e.shiftKey); break;
      case "PageUp": e.preventDefault(); move(-20, 0, e.shiftKey); break;
      case "PageDown": e.preventDefault(); move(20, 0, e.shiftKey); break;
      case "Home": e.preventDefault(); goTo({ v: sel?.a.v ?? 0, c: 0 }, e.shiftKey); break;
      case "End": e.preventDefault(); goTo({ v: sel?.a.v ?? 0, c: colCount - 1 }, e.shiftKey); break;
      case "Tab": e.preventDefault(); move(0, e.shiftKey ? -1 : 1, false); break;
      case "Enter": e.preventDefault(); move(e.shiftKey ? -1 : 1, 0, false); break;
      case "F2": e.preventDefault(); if (sel) startEdit(sel.a, activeText); break;
      case "Delete": case "Backspace": e.preventDefault(); clearRange(); break;
      default:
        if (sel && k.length === 1 && !e.altKey) { e.preventDefault(); startEdit(sel.a, k); }
    }
  }

  /** true when saved (or nothing to save). */
  async function save(): Promise<boolean> {
    commitEdit();                       // an edit still in the box is part of what is being saved
    const book = sheetsRef.current;
    if (!book || !dirtyRef.current) return true;
    setBusy("save");
    setError(null);
    try {
      // Blank rows left at the bottom are the grid's, not the workbook's - read_sheets() trims
      // them on the way in, so they are trimmed on the way out too. An all-empty ITEMS row
      // would otherwise reach the ERP as an item.
      const clean = book.map((s) => {
        const rows = [...s.rows];
        while (rows.length > 1 && !rows[rows.length - 1].some((c) => c.trim())) rows.pop();
        return { ...s, rows };
      });
      const d = await jobsApi.saveJobExcel(jobId, clean);
      setEdited(d.edited);
      dirtyRef.current = false;
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
    commitEdit();
    if (!window.confirm(dirtyRef.current ? `Save your changes and ${submit.label.toLowerCase()} now?` : `${submit.label} now?`)) return;
    if (!(await save())) return;
    setBusy("submit");
    onClose();
    submit.run();
  }
  function close() {
    if ((dirtyRef.current || editRef.current) && !window.confirm("You have unsaved changes. Close without saving?")) return;
    onClose();
  }

  // Ctrl/Cmd+S saves, Esc backs out of whatever is open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); if (!readOnly) save(); }
      else if (e.key === "Escape") {
        if (editRef.current) { cancelEdit(); focusGrid(); }
        else if (ctx) setCtx(null);
        else if (menuCol !== null) setMenuCol(null);
        else if (copied) setCopied(null);
        else close();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const zi = ZOOMS.indexOf(zoom);
  const nonEmpty = (s: jobsApi.ErpExcelSheet) => s.rows.slice(1).some((r) => r.some((c) => c.trim()));
  const tabs = (sheets ?? []).map((s, i) => ({ s, i })).filter(({ s, i }) => !onlyFilled || nonEmpty(s) || i === active);

  // ---- what the status bar says about the selection ----
  const stats = useMemo(() => {
    if (!rect || !sheet) return null;
    const cells = (rect.v1 - rect.v0 + 1) * (rect.c1 - rect.c0 + 1);
    let filled = 0;
    let numbers = 0;
    let sum = 0;
    for (let v = rect.v0; v <= rect.v1; v++) {
      for (let c = rect.c0; c <= rect.c1; c++) {
        const t = textAt(v, c).trim();
        if (!t) continue;
        filled++;
        const n = Number(t.replace(/,/g, ""));
        if (Number.isFinite(n)) { numbers++; sum += n; }
      }
    }
    return { cells, filled, numbers, sum };
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [rect?.v0, rect?.v1, rect?.c0, rect?.c1, sheet, view]);

  const fillPreview = drag.current === "fill" && rect && fillTo ? fillRect(rect, fillTo) : null;
  const tableWidth = GUTTER_W + PAD_COLS * DEFAULT_COL_W
    + Array.from({ length: colCount }, (_, c) => widthOf(c)).reduce((a, b) => a + b, 0);
  const padTitle = `This sheet ends at column ${colName(Math.max(0, colCount - 1))} — the ERP import maps columns by the header row, so a value here could not be saved.`;
  const editInCell = !!editAt && !editRef.current?.inBar;

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
              <button type="button" onClick={save} disabled={busy !== null || (!dirty && !editAt)} title="Save (Ctrl+S)"
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
          {!readOnly && (
            <div className="flex items-center rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800">
              <button type="button" onClick={undo} disabled={!past.length} title="Undo (Ctrl+Z)"
                className="p-1.5 text-slate-600 hover:bg-slate-100 disabled:opacity-40 dark:text-slate-300 dark:hover:bg-slate-700">
                <UndoIcon className="h-4 w-4" />
              </button>
              <button type="button" onClick={redo} disabled={!future.length} title="Redo (Ctrl+Y)"
                className="border-l border-slate-200 p-1.5 text-slate-600 hover:bg-slate-100 disabled:opacity-40 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-700">
                <RedoIcon className="h-4 w-4" />
              </button>
            </div>
          )}
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
          Filter on {filterCount} column{filterCount > 1 ? "s" : ""} — showing {bodyRows.length} of {Math.max(0, sheet.rows.length - 1)} rows.
          Filters only change what you see; Save and Submit always use every row.
          <button type="button" onClick={clearFilters} className="ml-auto rounded px-2 py-0.5 font-medium hover:bg-indigo-100 dark:hover:bg-indigo-500/20">Clear filters</button>
        </div>
      )}

      {/* Formula bar - at 127 columns a cell shows a fraction of what is in it */}
      {sheet && (
        <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-3 py-1.5 dark:border-slate-800 dark:bg-slate-900">
          <span className="w-20 shrink-0 rounded border border-slate-200 bg-slate-50 px-2 py-1 text-center text-xs font-medium text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">
            {sel ? `${colName(sel.a.c)}${realRow(sel.a.v) + 1}` : "—"}
          </span>
          <span className="shrink-0 text-xs text-slate-300 dark:text-slate-600">fx</span>
          <input
            value={editAt ? editValue : activeText}
            readOnly={!canEditCell || !sel}
            placeholder={sel ? "" : "Click a cell"}
            onChange={(e) => {
              if (!sel) return;
              if (editRef.current) { editRef.current.value = e.target.value; setEditValue(e.target.value); }
              else startEdit(sel.a, e.target.value, true);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); commitEdit(); move(1, 0, false); focusGrid(); }
              else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancelEdit(); focusGrid(); }
            }}
            onBlur={() => { if (editRef.current?.inBar) commitEdit(); }}
            className="min-w-0 flex-1 rounded border border-transparent bg-transparent px-2 py-1 font-mono text-xs text-slate-800 outline-none read-only:text-slate-500 focus:border-slate-200 focus:bg-white focus:ring-2 focus:ring-indigo-300 dark:text-slate-100 dark:focus:border-slate-600 dark:focus:bg-slate-800"
          />
        </div>
      )}

      {/* Grid */}
      <div
        ref={gridRef}
        tabIndex={0}
        onKeyDown={onGridKey}
        onCopy={onCopy}
        onCut={onCut}
        onPaste={onPaste}
        className="min-h-0 flex-1 overflow-auto bg-white outline-none dark:bg-slate-900"
      >
        {!sheets && !error && <p className="p-5 text-sm text-slate-500">Opening…</p>}
        {sheet && colCount > 0 && (
          <div style={{ zoom }}>
            <table style={{ tableLayout: "fixed", width: tableWidth }} className="select-none border-separate border-spacing-0 text-xs">
              <colgroup>
                <col style={{ width: GUTTER_W }} />
                {Array.from({ length: colCount }, (_, c) => <col key={c} style={{ width: widthOf(c) }} />)}
                {Array.from({ length: PAD_COLS }, (_, i) => <col key={`pad${i}`} style={{ width: DEFAULT_COL_W }} />)}
              </colgroup>
              <thead>
                {/* Column letters: click one to take the whole column, drag its edge to resize */}
                <tr>
                  <th className="sticky left-0 top-0 z-30 h-7 border-b border-r border-slate-200 bg-slate-100 p-0 dark:border-slate-700 dark:bg-slate-800">
                    <button type="button" onClick={selectAll} title="Select every cell (Ctrl+A)" className="h-7 w-full" />
                  </th>
                  {Array.from({ length: colCount }, (_, c) => {
                    const on = !!rect && c >= rect.c0 && c <= rect.c1;
                    return (
                      <th key={c} onMouseDown={() => selectColumn(c)} title={`Column ${colName(c)} — click to select it, drag the edge to resize, double-click the edge to fit`}
                        className={`sticky top-0 z-20 h-7 cursor-pointer border-b border-r border-slate-200 px-2 py-1 font-medium dark:border-slate-700 ${on ? "bg-indigo-100 text-indigo-800 dark:bg-indigo-500/25 dark:text-indigo-100" : "bg-slate-100 text-slate-500 dark:bg-slate-800"}`}>
                        <span className="relative block">
                          {colName(c)}
                          <span aria-hidden onMouseDown={(e) => startResize(e, c)} onDoubleClick={(e) => { e.stopPropagation(); autoFit(c); }}
                            className="absolute -right-2 -top-1 h-7 w-3 cursor-col-resize" />
                        </span>
                      </th>
                    );
                  })}
                  {Array.from({ length: PAD_COLS }, (_, i) => (
                    <th key={`pad${i}`} className="sticky top-0 z-20 h-7 border-b border-r border-slate-200 bg-slate-100/60 px-2 py-1 font-medium text-slate-300 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-600">
                      {colName(colCount + i)}
                    </th>
                  ))}
                </tr>
                {/* Row 1 - the header the ERP import maps by, so it is never editable */}
                {sheet.rows.length > 0 && (
                  <tr>
                    <th className="sticky left-0 top-7 z-30 border-b border-r border-slate-200 bg-slate-50 px-1.5 text-right font-normal text-slate-400 shadow-[2px_0_0_0_rgba(148,163,184,0.25)] dark:border-slate-700 dark:bg-slate-800">1</th>
                    {Array.from({ length: colCount }, (_, c) => {
                      const head = sheet.rows[0][c] ?? "";
                      const on = !!colFilters[c];
                      return (
                        <th key={c} className={`sticky top-7 ${menuCol === c ? "z-40" : "z-20"} border-b border-r border-slate-200 bg-slate-50 p-0 text-left dark:border-slate-700 dark:bg-slate-800`}>
                          <div className="relative flex items-center">
                            <span title={`${head} (header - locked)`} className="min-w-0 flex-1 truncate px-1.5 py-1 font-semibold text-slate-800 dark:text-slate-100">{head}</span>
                            <button type="button" onClick={() => setMenuCol(menuCol === c ? null : c)} title={on ? "Filtered - change or clear" : "Filter this column"}
                              className={`mr-1 shrink-0 rounded p-0.5 ${on ? "bg-indigo-600 text-white" : "text-slate-400 hover:bg-slate-200 hover:text-slate-700 dark:hover:bg-slate-700 dark:hover:text-slate-200"}`}>
                              <FunnelIcon className="h-3.5 w-3.5" />
                            </button>
                            {menuCol === c && (
                              <FilterMenu values={distinct(c)} allowed={colFilters[c]} onApply={(a) => setColFilter(c, a)} onClose={() => setMenuCol(null)} />
                            )}
                          </div>
                        </th>
                      );
                    })}
                    {Array.from({ length: PAD_COLS }, (_, i) => (
                      <th key={`pad${i}`} className="sticky top-7 z-20 border-b border-r border-slate-200 bg-slate-50/60 dark:border-slate-700 dark:bg-slate-800/50" />
                    ))}
                  </tr>
                )}
              </thead>
              <tbody onMouseDown={onBodyMouseDown} onClick={onBodyClick} onMouseOver={onBodyMouseOver}
                onDoubleClick={onBodyDoubleClick} onContextMenu={onBodyContextMenu}>
                {filterCount > 0 && bodyRows.length === 0 && (
                  <tr><td colSpan={colCount + PAD_COLS + 1} className="border-b border-slate-200 px-3 py-4 text-slate-500 dark:border-slate-700">No rows match the filter — the blank rows below are still yours to type in.</td></tr>
                )}
                {view.map((r, v) => {
                  const [selC0, selC1] = spanIn(rect, v);
                  const [fillC0, fillC1] = spanIn(fillPreview, v);
                  const [copC0, copC1] = spanIn(copied, v);
                  return (
                    <GridRow
                      key={r}
                      v={v}
                      r={r}
                      cells={sheet.rows[r]}
                      colCount={colCount}
                      beyond={r >= (sheet.rows.length || 1)}
                      readOnly={readOnly}
                      rowSelected={selC0 >= 0}
                      padTitle={padTitle}
                      selC0={selC0} selC1={selC1}
                      fillC0={fillC0} fillC1={fillC1}
                      copC0={copC0} copC1={copC1}
                      activeC={sel && sel.a.v === v ? sel.a.c : -1}
                      handleC={!readOnly && rect && v === rect.v1 ? rect.c1 : -1}
                      editC={editAt && editAt.v === v ? editAt.c : -1}
                      editValue={editAt && editAt.v === v ? editValue : ""}
                      editInCell={editInCell}
                      onEditChange={onEditChange}
                      onEditBlur={onEditBlur}
                      onEditKey={onEditKey}
                    />
                  );
                })}
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
        {sheet && colCount === 0 && <p className="p-5 text-sm text-slate-500">This sheet is empty.</p>}
      </div>

      {/* Status bar */}
      {sheet && (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-slate-200 bg-white px-4 py-1.5 text-[11px] text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400">
          {stats && stats.cells > 1 && rect && (
            <>
              <span>{rect.v1 - rect.v0 + 1} R × {rect.c1 - rect.c0 + 1} C</span>
              <span>Count {stats.filled}</span>
              {stats.numbers > 0 && <span>Sum {stats.sum.toLocaleString(undefined, { maximumFractionDigits: 4 })}</span>}
              {stats.numbers > 0 && <span>Average {(stats.sum / stats.numbers).toLocaleString(undefined, { maximumFractionDigits: 4 })}</span>}
            </>
          )}
          <span className="text-slate-400 dark:text-slate-500">
            Drag the blue square to fill · Ctrl+C / Ctrl+V with Excel · Ctrl+Z undo · F2 or double-click to edit
          </span>
          <span className="ml-auto">
            {Math.max(0, (sheet.rows.length || 1) - 1)} data row{sheet.rows.length === 2 ? "" : "s"} · {colCount} column{colCount === 1 ? "" : "s"}
          </span>
        </div>
      )}

      {/* Right-click menu */}
      {ctx && (
        <>
          <div className="fixed inset-0 z-[60]" onMouseDown={() => setCtx(null)} onContextMenu={(e) => { e.preventDefault(); setCtx(null); }} />
          <div style={{ left: Math.min(ctx.x, window.innerWidth - 210), top: Math.min(ctx.y, window.innerHeight - 230) }}
            className="fixed z-[61] w-52 rounded-lg border border-slate-200 bg-white py-1 text-xs text-slate-700 shadow-xl dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200">
            {[
              { label: "Copy", key: "copy", on: !!rect },
              { label: "Clear contents", key: "clear", on: !readOnly && !!rect },
              { label: "—", key: "sep", on: true },
              { label: "Insert row above", key: "above", on: !readOnly && realRow(ctx.v) >= 1 },
              { label: "Insert row below", key: "below", on: !readOnly },
              { label: "Delete this row", key: "del", on: !readOnly && realRow(ctx.v) < (sheet?.rows.length ?? 0) },
              { label: "—", key: "sep2", on: true },
              { label: "Fit this column", key: "fit", on: true },
            ].map((item) =>
              item.label === "—" ? (
                <div key={item.key} className="my-1 border-t border-slate-100 dark:border-slate-700" />
              ) : (
                <button key={item.key} type="button" disabled={!item.on}
                  onClick={() => {
                    const r = realRow(ctx.v);
                    if (item.key === "copy" && rect) { try { void navigator.clipboard.writeText(toTsv(block(rect))); setCopied(rect); } catch { /* the browser said no */ } }
                    if (item.key === "clear") clearRange();
                    if (item.key === "above") insertRow(Math.max(1, r));
                    if (item.key === "below") insertRow(Math.min(sheet?.rows.length ?? 1, r + 1));
                    if (item.key === "del") delRow(r);
                    if (item.key === "fit") autoFit(ctx.c);
                    setCtx(null);
                    focusGrid();
                  }}
                  className="block w-full px-3 py-1.5 text-left hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent dark:hover:bg-slate-700">
                  {item.label}
                </button>
              ),
            )}
          </div>
        </>
      )}
    </div>
  );
}
