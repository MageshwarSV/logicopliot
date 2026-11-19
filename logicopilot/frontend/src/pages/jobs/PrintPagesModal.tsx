import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import * as jobsApi from "../../api/jobs";
import type { JobDocument } from "../../types/jobs";

/** Document Capture -> "Print pages...": for the occasional print that must leave out some
 *  pages. Every document's pages as thumbnails; nothing is ticked to start - tick exactly the
 *  pages to print. Print only: the job's documents are not changed. Whole-document printing
 *  stays on the ordinary Print button (PrintDocumentsModal). */

type PageItem =
  | { kind: "doc"; key: string; docId: string; page: number }
  | { kind: "loose"; key: string; pageId: string };

function Thumb({ load, onOpen }: { load: () => Promise<string>; onOpen: (url: string) => void }) {
  const ref = useRef<HTMLButtonElement | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let done = false;
    const io = new IntersectionObserver((entries) => {
      if (done || !entries.some((e) => e.isIntersecting)) return;
      done = true;
      io.disconnect();
      load().then(setUrl).catch(() => setUrl(""));
    }, { rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <button ref={ref} type="button" onClick={() => url && onOpen(url)}
      className="flex h-32 w-24 items-center justify-center overflow-hidden rounded border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800"
      title="Click to see the page full size">
      {url ? <img src={url} alt="" className="h-full w-full object-contain" /> : <span className="text-[10px] text-slate-400">{url === "" ? "no image" : "…"}</span>}
    </button>
  );
}

export function PrintPagesModal({
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
  const classified = useMemo(
    () => slots.map((s) => ({ ...s, files: s.files.filter((f) => f.is_uploaded) })).filter((s) => s.files.length > 0),
    [slots],
  );
  // Every printable page, in print order: slots in template order, each document's pages, then
  // the Unclassified pages in their on-screen order.
  const docItems = useMemo(() => classified.flatMap((s) => s.files.flatMap((f) =>
    f.source_kind === "excel" ? [] : Array.from({ length: f.page_count || 0 }, (_, i) => (
      { kind: "doc" as const, key: `${f.id}:${i + 1}`, docId: f.id, page: i + 1 }))),
  ), [classified]);
  const looseItems: PageItem[] = useMemo(() => pages.map((p) => ({ kind: "loose" as const, key: `u:${p.id}`, pageId: p.id })), [pages]);
  const excelDocIds = classified.flatMap((s) => s.files.filter((f) => f.source_kind === "excel").map((f) => f.id));

  const [picked, setPicked] = useState<Set<string>>(new Set());          // page keys, Excel doc ids, "x:<file id>"
  const [busy, setBusy] = useState<"print" | "download" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [big, setBig] = useState<string | null>(null);

  const flip = (keys: string[], on?: boolean) => {
    const n = new Set(picked);
    const turnOn = on ?? !keys.every((k) => n.has(k));
    keys.forEach((k) => (turnOn ? n.add(k) : n.delete(k)));
    setPicked(n);
  };
  const classifiedKeys = [...docItems.map((i) => i.key), ...excelDocIds];
  const unclassifiedKeys = [...looseItems.map((i) => i.key), ...files.map((f) => `x:${f.id}`)];
  const pageCount = [...picked].filter((k) => k.includes(":") && !k.startsWith("x:")).length;
  const sheetCount = picked.size - pageCount;

  async function build(kind: "print" | "download") {
    if (picked.size === 0) {
      setError("Tick at least one page.");
      return;
    }
    setBusy(kind);
    setError(null);
    try {
      const documentPages: Record<string, number[]> = {};
      for (const it of docItems) {
        if (picked.has(it.key)) (documentPages[it.docId] ??= []).push(it.page);
      }
      const documents = classified.flatMap((s) => s.files.map((f) => f.id))
        .filter((id) => documentPages[id] || picked.has(id));
      const blob = await jobsApi.printDocuments(jobId, {
        documents,
        document_pages: documentPages,
        unclassified_pages: looseItems.filter((i) => picked.has(i.key)).map((i) => (i as { pageId: string }).pageId),
        unclassified_files: files.filter((f) => picked.has(`x:${f.id}`)).map((f) => f.id),
      });
      const url = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
      if (kind === "download") {
        const a = document.createElement("a");
        a.href = url;
        a.download = `${jobReference}-pages.pdf`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
      } else {
        const frame = document.createElement("iframe");
        Object.assign(frame.style, { position: "fixed", width: "0", height: "0", border: "0" });
        frame.src = url;
        frame.onload = () => {
          try { frame.contentWindow?.focus(); frame.contentWindow?.print(); } catch { window.open(url, "_blank"); }
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

  const tile = (key: string, label: string, thumb: React.ReactNode) => (
    <div key={key} className="flex flex-col items-center gap-1">
      {thumb}
      <label className="flex items-center gap-1 text-[11px] text-slate-600 dark:text-slate-300">
        <input type="checkbox" checked={picked.has(key)} onChange={() => flip([key])} /> {label}
      </label>
    </div>
  );
  const btn = "rounded-md border border-slate-200 px-2 py-1 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="flex max-h-[92vh] w-full max-w-5xl flex-col rounded-xl bg-white shadow-xl dark:bg-slate-900" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-700">
          <div>
            <h3 className="text-base font-semibold text-slate-900 dark:text-slate-50">Print pages</h3>
            <p className="text-xs text-slate-500">Tick only the pages to print. The job's documents are not changed.</p>
          </div>
          <button onClick={onClose} className="text-sm text-slate-500 hover:text-slate-800 dark:hover:text-slate-200">Close</button>
        </div>
        <div className="flex flex-wrap gap-2 border-b border-slate-200 px-5 py-2 text-xs dark:border-slate-700">
          <button onClick={() => flip([...classifiedKeys, ...unclassifiedKeys], true)} className={btn}>Select all</button>
          <button onClick={() => flip(classifiedKeys, true)} className={btn}>Select all classified</button>
          <button onClick={() => flip(unclassifiedKeys, true)} className={btn}>Select all unclassified</button>
          <button onClick={() => setPicked(new Set())} className="rounded-md px-2 py-1 text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-800">Clear</button>
          <span className="ml-auto self-center text-slate-500">
            {pageCount} page{pageCount === 1 ? "" : "s"}{sheetCount ? ` + ${sheetCount} Excel` : ""} selected
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3 text-sm">
          {classified.map((s) => (
            <div key={s.tdocId} className="mb-4">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-slate-500">{s.name}</p>
              {s.files.map((f) => {
                const keys = f.source_kind === "excel" ? [f.id]
                  : Array.from({ length: f.page_count || 0 }, (_, i) => `${f.id}:${i + 1}`);
                return (
                  <div key={f.id} className="mb-3 rounded-lg border border-slate-100 p-2 dark:border-slate-800">
                    <div className="mb-2 flex items-center gap-2">
                      <span className="min-w-0 truncate font-medium text-slate-700 dark:text-slate-200" title={f.original_name ?? ""}>{f.original_name ?? "Document"}</span>
                      <button onClick={() => flip(keys)} className="shrink-0 text-xs text-indigo-600 hover:underline">
                        {keys.every((k) => picked.has(k)) ? "Unselect pages" : "Select all pages"}
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-3">
                      {f.source_kind === "excel"
                        ? tile(f.id, `Sheet ${f.sheet_name ?? ""}`, <div className="flex h-32 w-24 items-center justify-center rounded border border-emerald-200 bg-emerald-50 text-[11px] font-semibold text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">Excel sheet</div>)
                        : keys.map((k, i) => tile(k, `Page ${i + 1}`,
                            <Thumb load={() => jobsApi.jobDocPageUrl(jobId, f.id, i + 1)} onOpen={setBig} />))}
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
          {(looseItems.length > 0 || files.length > 0) && (
            <div className="mb-2">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-slate-500">Unclassified</p>
              <div className="flex flex-wrap gap-3">
                {pages.map((p) => tile(`u:${p.id}`, `${p.group_label ? `${p.group_label} · ` : ""}${p.original_filename.slice(0, 18)} p${p.page_number}`,
                  <Thumb load={() => jobsApi.unclassifiedPageImageUrl(jobId, p.id)} onOpen={setBig} />))}
                {files.map((f) => tile(`x:${f.id}`, f.original_filename.slice(0, 22),
                  <div className="flex h-32 w-24 items-center justify-center rounded border border-emerald-200 bg-emerald-50 px-1 text-center text-[11px] font-semibold text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">Excel file</div>))}
              </div>
            </div>
          )}
          {classified.length === 0 && looseItems.length === 0 && files.length === 0 && (
            <p className="text-xs text-slate-400">No documents on this job yet.</p>
          )}
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
      {big && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-6" onClick={(e) => { e.stopPropagation(); setBig(null); }}>
          <img src={big} alt="" className="max-h-full max-w-full rounded bg-white" />
        </div>
      )}
    </div>
  );
}
