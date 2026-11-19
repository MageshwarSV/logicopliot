import { useMemo, useState } from "react";
import axios from "axios";
import * as jobsApi from "../../api/jobs";
import type { JobDocument } from "../../types/jobs";

/** Document Capture -> Print: choose which documents to print - placed (classified) documents,
 *  Unclassified pages and unused Excel files - and get them as ONE PDF, to print or download.
 *  Classified documents come first, in slot order, then the unclassified ones. */
export function PrintDocumentsModal({
  jobId,
  jobReference,
  slots,
  pages,
  files,
  onClose,
}: {
  jobId: string;
  jobReference: string;
  slots: { tdocId: string; name: string; files: JobDocument[] }[];
  pages: jobsApi.UnclassifiedPage[];
  files: jobsApi.UnclassifiedFile[];
  onClose: () => void;
}) {
  // Unclassified pages, one row per heading ("Air Waybill") or per file - not per page.
  const pageGroups = useMemo(() => {
    const order: string[] = [];
    const by: Record<string, jobsApi.UnclassifiedPage[]> = {};
    for (const p of pages) {
      const key = p.group_label ? `${p.group_label} (house waybills)` : p.original_filename;
      if (!by[key]) {
        by[key] = [];
        order.push(key);
      }
      by[key].push(p);
    }
    return order.map((k) => ({ key: k, pages: by[k] }));
  }, [pages]);
  const classified = useMemo(
    () => slots.map((s) => ({ ...s, files: s.files.filter((f) => f.is_uploaded) })).filter((s) => s.files.length > 0),
    [slots],
  );
  const allDocIds = classified.flatMap((s) => s.files.map((f) => f.id));
  const [docs, setDocs] = useState<Set<string>>(new Set());
  const [groups, setGroups] = useState<Set<string>>(new Set());
  const [xls, setXls] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<"print" | "download" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, id: string) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id); else next.add(id);
    setter(next);
  };
  const selectClassified = () => setDocs(new Set(allDocIds));
  const selectUnclassified = () => {
    setGroups(new Set(pageGroups.map((g) => g.key)));
    setXls(new Set(files.map((f) => f.id)));
  };
  const count = docs.size + groups.size + xls.size;

  async function build(kind: "print" | "download") {
    if (count === 0) {
      setError("Choose at least one document.");
      return;
    }
    setBusy(kind);
    setError(null);
    try {
      const blob = await jobsApi.printDocuments(jobId, {
        documents: allDocIds.filter((id) => docs.has(id)),                     // slot order
        unclassified_pages: pageGroups.filter((g) => groups.has(g.key)).flatMap((g) => g.pages.map((p) => p.id)),
        unclassified_files: files.filter((f) => xls.has(f.id)).map((f) => f.id),
      });
      const url = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
      if (kind === "download") {
        const a = document.createElement("a");
        a.href = url;
        a.download = `${jobReference}-documents.pdf`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
      } else {
        // Print straight from a hidden frame; a browser that blocks that gets the PDF in a tab.
        const frame = document.createElement("iframe");
        frame.style.position = "fixed";
        frame.style.width = "0";
        frame.style.height = "0";
        frame.style.border = "0";
        frame.src = url;
        frame.onload = () => {
          try {
            frame.contentWindow?.focus();
            frame.contentWindow?.print();
          } catch {
            window.open(url, "_blank");
          }
        };
        document.body.appendChild(frame);
        setTimeout(() => { frame.remove(); URL.revokeObjectURL(url); }, 5 * 60 * 1000);
      }
    } catch (err) {
      let msg = "Could not build the PDF.";
      if (axios.isAxiosError(err) && err.response?.data instanceof Blob) {
        try { msg = JSON.parse(await err.response.data.text()).detail ?? msg; } catch { /* keep default */ }
      }
      setError(msg);
    } finally {
      setBusy(null);
    }
  }

  const box = "h-4 w-4 shrink-0 rounded border-slate-300";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="flex max-h-[88vh] w-full max-w-3xl flex-col rounded-xl bg-white shadow-xl dark:bg-slate-900" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-700">
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-50">Print documents</h3>
          <button onClick={onClose} className="text-sm text-slate-500 hover:text-slate-800 dark:hover:text-slate-200">Close</button>
        </div>
        <div className="flex flex-wrap gap-2 border-b border-slate-200 px-5 py-2 text-xs dark:border-slate-700">
          <button onClick={() => { selectClassified(); selectUnclassified(); }} className="rounded-md border border-slate-200 px-2 py-1 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800">Select all</button>
          <button onClick={selectClassified} disabled={allDocIds.length === 0}
            title={allDocIds.length === 0 ? "Nothing classified on this job" : undefined}
            className="rounded-md border border-slate-200 px-2 py-1 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:hover:bg-slate-800">Select all classified</button>
          <button onClick={selectUnclassified} disabled={pageGroups.length === 0 && files.length === 0}
            title={pageGroups.length === 0 && files.length === 0 ? "Nothing unclassified on this job" : undefined}
            className="rounded-md border border-slate-200 px-2 py-1 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:hover:bg-slate-800">Select all unclassified</button>
          <button onClick={() => { setDocs(new Set()); setGroups(new Set()); setXls(new Set()); }} className="rounded-md px-2 py-1 text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-800">Clear</button>
          <span className="ml-auto self-center text-slate-500">{count} selected</span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3 text-sm">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Classified documents</p>
          {classified.length === 0 && <p className="mb-3 text-xs text-slate-400">None.</p>}
          {classified.map((s) => {
            const ids = s.files.map((f) => f.id);
            const all = ids.every((id) => docs.has(id));
            return (
              <div key={s.tdocId} className="mb-3">
                <label className="flex items-center gap-2 font-medium text-slate-800 dark:text-slate-100">
                  <input type="checkbox" className={box} checked={all}
                    onChange={() => { const n = new Set(docs); ids.forEach((id) => (all ? n.delete(id) : n.add(id))); setDocs(n); }} />
                  {s.name} <span className="text-xs font-normal text-slate-400">· {s.files.length}</span>
                </label>
                <div className="ml-6 mt-1 space-y-0.5">
                  {s.files.map((f) => (
                    <label key={f.id} className="flex items-center gap-2 text-slate-600 dark:text-slate-300">
                      <input type="checkbox" className={box} checked={docs.has(f.id)} onChange={() => toggle(docs, setDocs, f.id)} />
                      <span className="min-w-0 truncate" title={f.original_name ?? ""}>{f.original_name ?? "Document"}</span>
                      <span className="shrink-0 text-xs text-slate-400">{f.source_kind === "excel" ? "Excel sheet" : `${f.page_count} page${f.page_count === 1 ? "" : "s"}`}</span>
                    </label>
                  ))}
                </div>
              </div>
            );
          })}
          <p className="mb-2 mt-4 text-xs font-semibold uppercase tracking-wider text-slate-500">Unclassified</p>
          {pageGroups.length === 0 && files.length === 0 && <p className="text-xs text-slate-400">None.</p>}
          <div className="space-y-0.5">
            {pageGroups.map((g) => (
              <label key={g.key} className="flex items-center gap-2 text-slate-600 dark:text-slate-300">
                <input type="checkbox" className={box} checked={groups.has(g.key)} onChange={() => toggle(groups, setGroups, g.key)} />
                <span className="min-w-0 truncate" title={g.key}>{g.key}</span>
                <span className="shrink-0 text-xs text-slate-400">{g.pages.length} page{g.pages.length === 1 ? "" : "s"}</span>
              </label>
            ))}
            {files.map((f) => (
              <label key={f.id} className="flex items-center gap-2 text-slate-600 dark:text-slate-300">
                <input type="checkbox" className={box} checked={xls.has(f.id)} onChange={() => toggle(xls, setXls, f.id)} />
                <span className="rounded bg-emerald-100 px-1 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300">Excel</span>
                <span className="min-w-0 truncate" title={f.original_filename}>{f.original_filename}</span>
                <span className="shrink-0 text-xs text-slate-400">{f.sheets.length} sheet{f.sheets.length === 1 ? "" : "s"}</span>
              </label>
            ))}
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-slate-200 px-5 py-3 dark:border-slate-700">
          {error && <p className="mr-auto text-xs text-rose-600">{error}</p>}
          <button onClick={() => build("download")} disabled={busy !== null}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800">
            {busy === "download" ? "Building…" : "Download PDF"}
          </button>
          <button onClick={() => build("print")} disabled={busy !== null}
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
            {busy === "print" ? "Building…" : "Print"}
          </button>
        </div>
      </div>
    </div>
  );
}
