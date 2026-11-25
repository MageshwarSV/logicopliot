import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import * as erpApi from "../../api/onboardingErp";
import type { DocMapping, DocMappingField, ErpCustomer } from "../../api/onboardingErp";
import { OnboardingCustomer, OnboardingSteps, useOnboardingCustomer, useOnboardingFullScreen } from "../../components/onboarding/OnboardingSteps";

// Customer Onboarding - Step 2: Document Mapping. One sample per customer document; each of its
// Document fields (from Step 1) gets a box drawn where its value sits. The value read inside
// the box is shown next to the ERP Excel's value. Only drawn fields get a box.

function errorText(err: unknown, fallback: string): string {
  return (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
}

function fieldName(f: DocMappingField): string {
  return f.excel_name;
}

function norm(v: string): string {
  return v.toLowerCase().replace(/[\s,]+/g, "").replace(/[^a-z0-9./-]/g, "");
}

/** Does the value read inside the box agree with the ERP Excel's values for the column? */
function compare(f: DocMappingField): "match" | "differs" | "no-excel" | "no-read" | null {
  if (!f.mark) return null;
  const read = (f.mark.detected_value ?? "").trim();
  if (!read) return "no-read";
  if (!f.samples.length) return "no-excel";
  const r = norm(read);
  const rn = Number(read.replace(/,/g, ""));
  const ok = f.samples.some((s) => {
    const e = norm(s);
    const en = Number(String(s).replace(/,/g, ""));
    if (!Number.isNaN(rn) && !Number.isNaN(en) && read.trim() !== "" && String(s).trim() !== "") return rn === en;
    return e === r || (e.length > 3 && (r.includes(e) || e.includes(r)));
  });
  return ok ? "match" : "differs";
}

const DOC_ICON = (name: string) => {
  const n = name.toLowerCase();
  if (n.includes("lading") || n === "bl" || n.includes("waybill")) return "⚓";
  if (n.includes("packing") || n.includes("paackage") || n.includes("package")) return "📦";
  if (n.includes("invoice")) return "🧾";
  return "📄";
};

type Box = { id: string; label: string; x: number; y: number; width: number; height: number; selected: boolean; tone: string };

/** A sample page with the mapped fields' boxes; drag to draw a box for the selected field. */
function SampleCanvas({
  workbookId, docId, page, boxes, canDraw, onDraw, onPick,
}: {
  workbookId: string;
  docId: string;
  page: number;
  boxes: Box[];
  canDraw: boolean;
  onDraw: (b: { x: number; y: number; width: number; height: number }) => void;
  onPick: (id: string) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [draft, setDraft] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [showBoxes, setShowBoxes] = useState(true);

  useEffect(() => {
    let made: string | null = null;
    let cancelled = false;
    setUrl(null);
    setFailed(false);
    erpApi.fetchSamplePageUrl(workbookId, docId, page)
      .then((u) => {
        if (cancelled) URL.revokeObjectURL(u);
        else { made = u; setUrl(u); }
      })
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [workbookId, docId, page]);

  const rel = (e: React.MouseEvent) => {
    const r = wrap.current!.getBoundingClientRect();
    return { x: Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1), y: Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1) };
  };
  const finish = () => {
    if (!draft) return;
    const w = Math.abs(draft.x1 - draft.x0);
    const h = Math.abs(draft.y1 - draft.y0);
    setDraft(null);
    if (w >= 0.005 && h >= 0.005) onDraw({ x: Math.min(draft.x0, draft.x1), y: Math.min(draft.y0, draft.y1), width: w, height: h });
  };

  if (failed) return <div className="flex h-96 items-center justify-center rounded-xl border border-slate-200 text-sm text-slate-500">Could not load this page.</div>;
  if (!url) return <div className="flex h-96 items-center justify-center rounded-xl border border-slate-200 text-sm text-slate-400">Loading page…</div>;

  return (
    <div
      ref={wrap}
      onMouseDown={(e) => {
        if (!canDraw) return;
        e.preventDefault();
        const p = rel(e);
        setDraft({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
      }}
      onMouseMove={(e) => draft && setDraft((d) => (d ? { ...d, ...(() => { const p = rel(e); return { x1: p.x, y1: p.y }; })() } : d))}
      onMouseUp={finish}
      onMouseLeave={() => draft && finish()}
      className={`relative select-none overflow-hidden rounded-xl border border-slate-300 bg-slate-100 dark:border-slate-700 ${canDraw ? "cursor-crosshair" : ""}`}
    >
      <img src={url} alt={`Page ${page}`} className="block w-full" draggable={false} />
      {boxes.length > 0 && (
        <button
          type="button"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={() => setShowBoxes((v) => !v)}
          className="absolute right-2 top-2 z-20 rounded-md bg-white/90 px-2 py-1 text-[11px] font-medium text-slate-700 shadow ring-1 ring-slate-300 dark:bg-slate-900/90 dark:text-slate-200"
        >
          {showBoxes ? `Hide fields (${boxes.length})` : `Show fields (${boxes.length})`}
        </button>
      )}
      {showBoxes && boxes.map((b) => (
        <div
          key={b.id}
          className="absolute rounded-[2px] border-2"
          style={{ left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.width * 100}%`, height: `${b.height * 100}%`,
            borderColor: b.tone, boxShadow: b.selected ? `0 0 0 3px ${b.tone}55` : undefined }}
        >
          <button
            type="button"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={() => onPick(b.id)}
            className={`absolute left-0 whitespace-nowrap rounded-sm px-1 text-[10px] font-semibold leading-[1.35] text-white ${b.y < 0.04 ? "top-0" : "-top-[15px]"} ${b.selected ? "" : "opacity-80"}`}
            style={{ backgroundColor: b.tone }}
          >
            {b.label}
          </button>
        </div>
      ))}
      {draft && (
        <div
          className="absolute rounded-sm border-2 border-dashed border-indigo-600 bg-indigo-500/10"
          style={{ left: `${Math.min(draft.x0, draft.x1) * 100}%`, top: `${Math.min(draft.y0, draft.y1) * 100}%`,
            width: `${Math.abs(draft.x1 - draft.x0) * 100}%`, height: `${Math.abs(draft.y1 - draft.y0) * 100}%` }}
        />
      )}
    </div>
  );
}

const TONE = { match: "#16a34a", differs: "#d97706", "no-excel": "#4f46e5", "no-read": "#dc2626" } as const;

export function ErpDocumentMappingPage() {
  const navigate = useNavigate();
  // Chosen in Step 0 (Choose Template) and carried here.
  const groupId = useOnboardingCustomer("customer");
  const [customers, setCustomers] = useState<ErpCustomer[]>([]);
  const [data, setData] = useState<DocMapping | null>(null);
  const [loading, setLoading] = useState(false);
  const [docId, setDocId] = useState("");
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(100);
  const [selected, setSelected] = useState<string | null>(null);
  // Which fields the list shows - fields still to mark first, so the work left is what you see.
  const [view, setView] = useState<"todo" | "check" | "done" | "all">("todo");
  const [fieldSearch, setFieldSearch] = useState("");
  const [fullScreen, setFullScreen] = useOnboardingFullScreen();
  const [message, setMessage] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);

  const customer = customers.find((c) => c.group_id === groupId) ?? null;
  const workbookId = customer?.workbook?.id ?? "";

  useEffect(() => {
    erpApi.listCustomers().then(setCustomers).catch(() => setMessage({ tone: "warn", text: "Could not load the customers." }));
  }, []);

  useEffect(() => {
    if (!workbookId) { setData(null); return; }
    setLoading(true);
    erpApi.getDocMapping(workbookId)
      .then((d) => {
        setData(d);
        setDocId((cur) => (d.documents.some((x) => x.id === cur) ? cur : d.documents[0]?.id ?? ""));
      })
      .catch(() => setMessage({ tone: "warn", text: "Could not load the document mapping." }))
      .finally(() => setLoading(false));
  }, [workbookId]);

  useEffect(() => {
    if (!fullScreen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFullScreen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullScreen]);

  const doc = data?.documents.find((d) => d.id === docId) ?? null;
  const docFields = useMemo(() => (data?.fields ?? []).filter((f) => f.source_document === docId), [data, docId]);
  const countFor = (id: string) => {
    const fs = (data?.fields ?? []).filter((f) => f.source_document === id);
    return { total: fs.length, done: fs.filter((f) => f.mark).length };
  };
  const totals = (data?.documents ?? []).reduce((a, d) => { const c = countFor(d.id); return { total: a.total + c.total, done: a.done + c.done }; }, { total: 0, done: 0 });
  const unassigned = (data?.fields ?? []).filter((f) => !f.source_document || !data?.documents.some((d) => d.id === f.source_document));
  const selectedField = docFields.find((f) => f.id === selected) ?? null;
  const needsCheck = (f: DocMappingField) => !!f.mark && (compare(f) === "differs" || compare(f) === "no-read");
  const shownFields = docFields.filter((f) => {
    if (view === "todo" && f.mark) return false;
    if (view === "done" && !f.mark) return false;
    if (view === "check" && !needsCheck(f)) return false;
    const fq = fieldSearch.trim().toLowerCase();
    return !fq || `${f.excel_name} ${f.sheet_name}`.toLowerCase().includes(fq);
  });

  useEffect(() => { setPage(1); setSelected(null); }, [docId]);

  const replaceField = (id: string, patch: Partial<DocMappingField>) =>
    setData((d) => (d ? { ...d, fields: d.fields.map((f) => (f.id === id ? { ...f, ...patch } : f)) } : d));

  const draw = async (b: { x: number; y: number; width: number; height: number }) => {
    if (!selectedField) return;
    const lineDefault = selectedField.mark?.line_item ?? /item|line|product/i.test(selectedField.sheet_name);
    try {
      const mark = await erpApi.saveMark(selectedField.id, { page, ...b, line_item: lineDefault });
      replaceField(selectedField.id, { mark });
      // On to the next field without a box.
      const next = docFields.find((f) => !f.mark && f.id !== selectedField.id);
      setSelected(next?.id ?? null);
    } catch (err) {
      setMessage({ tone: "warn", text: errorText(err, "Could not save the box.") });
    }
  };

  const boxes: Box[] = docFields
    .filter((f) => f.mark && f.mark.page === page)
    .map((f) => ({ id: f.id, label: fieldName(f), x: f.mark!.x, y: f.mark!.y, width: f.mark!.width, height: f.mark!.height,
      selected: f.id === selected, tone: TONE[compare(f) ?? "no-excel"] }));

  return (
    <AppShell title="Document Mapping" subtitle="Customer Onboarding · Step 2 of 4 - mark where each document field sits on the sample">
      <div className={fullScreen ? "fixed inset-0 z-40 overflow-auto bg-slate-50 p-4 dark:bg-slate-950" : ""}>
        <OnboardingSteps current={2} groupId={groupId} />
        <Card className="mb-4 flex flex-wrap items-center gap-3 p-4">
          <OnboardingCustomer name={customer?.name} tenant={customer?.tenant} groupId={groupId} />
          {customer?.workbook && <span className="text-xs text-slate-400">Step 1: {customer.workbook.file_name} · {customer.workbook.percent}% classified</span>}
          <div className="ml-auto flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => setFullScreen((v) => !v)}>{fullScreen ? "✕ Close full screen" : "⛶ Full screen"}</Button>
          </div>
        </Card>

        {message && (
          <div className={`mb-4 rounded-lg border px-4 py-2 text-sm ${message.tone === "ok"
            ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200"
            : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"}`}>
            {message.text}
            <button type="button" className="ml-3 text-xs underline" onClick={() => setMessage(null)}>dismiss</button>
          </div>
        )}

        {!groupId && (
          <Card className="p-10 text-center text-sm text-slate-500">
            No customer template chosen yet. <Link className="text-indigo-600 underline" to="/super-admin/onboarding/start">Choose one in Step 0</Link>.
          </Card>
        )}
        {groupId && !customer?.workbook && customers.length > 0 && (
          <Card className="p-10 text-center text-sm text-slate-500">
            This customer has no ERP workbook yet. <Link className="text-indigo-600 underline" to={`/super-admin/onboarding/erp-fields?customer=${groupId}`}>Do Step 1 first</Link>.
          </Card>
        )}
        {loading && <Card className="p-10 text-center text-sm text-slate-500">Loading…</Card>}

        {data && !loading && (
          <div className="grid gap-4 xl:grid-cols-[15rem_minmax(0,1fr)_22rem]">
            {/* Documents */}
            <Card className="h-fit p-4">
              <p className="mb-2 text-sm font-semibold text-slate-800 dark:text-slate-100">Documents</p>
              <div className="flex flex-col gap-1">
                {data.documents.map((d) => {
                  const c = countFor(d.id);
                  const on = d.id === docId;
                  return (
                    <button
                      key={d.id}
                      type="button"
                      onClick={() => setDocId(d.id)}
                      className={`flex items-center gap-2 rounded-lg px-2 py-2 text-left text-sm ${on ? "bg-indigo-50 ring-1 ring-indigo-200 dark:bg-indigo-500/10 dark:ring-indigo-500/30" : "hover:bg-slate-50 dark:hover:bg-slate-800"}`}
                    >
                      <span className="text-lg">{DOC_ICON(d.name)}</span>
                      <span className="min-w-0 flex-1">
                        <span className={`block truncate font-semibold ${on ? "text-indigo-700 dark:text-indigo-300" : "text-slate-700 dark:text-slate-200"}`}>{d.name}</span>
                        <span className="block text-[11px] text-slate-400">{d.sample ? `${d.sample.page_count} page${d.sample.page_count === 1 ? "" : "s"}` : "no sample yet"}</span>
                      </span>
                      <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${c.total && c.done === c.total ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"}`}>
                        {c.done}/{c.total}
                      </span>
                    </button>
                  );
                })}
              </div>
              <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/40">
                <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">Fields marked</p>
                <div className="mt-1 flex justify-between text-sm"><span className="text-slate-600 dark:text-slate-300">{totals.done} / {totals.total}</span>
                  <span className="font-semibold">{totals.total ? Math.round((totals.done * 100) / totals.total) : 0}%</span></div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                  <div className="h-full rounded-full bg-emerald-500" style={{ width: `${totals.total ? (totals.done * 100) / totals.total : 0}%` }} />
                </div>
                <p className="mt-1 text-[11px] text-slate-400">Only fields you draw get a box - the rest stay unmarked.</p>
              </div>
              {unassigned.length > 0 && (
                <p className="mt-3 text-[11px] text-amber-600">
                  {unassigned.length} Document field(s) have no document in Step 1 - <Link className="underline" to={`/super-admin/onboarding/erp-fields?customer=${groupId}`}>assign them</Link>.
                </p>
              )}
            </Card>

            {/* Sample */}
            <Card className="min-w-0 p-4">
              {doc && (
                <>
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <p className="text-base font-semibold text-slate-900 dark:text-slate-50">{DOC_ICON(doc.name)} {doc.name}</p>
                    {doc.sample && <span className="truncate text-xs text-slate-400">{doc.sample.file_name}</span>}
                    <div className="ml-auto flex flex-wrap items-center gap-2">
                      {doc.sample && doc.sample.page_count > 1 && (
                        <div className="flex items-center gap-1 text-xs text-slate-500">
                          <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="rounded px-2 py-1 hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-slate-800">‹</button>
                          page {page} of {doc.sample.page_count}
                          <button type="button" disabled={page >= doc.sample.page_count} onClick={() => setPage((p) => p + 1)} className="rounded px-2 py-1 hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-slate-800">›</button>
                        </div>
                      )}
                      {doc.sample && (
                        <div className="flex items-center gap-1 text-xs text-slate-500">
                          <button type="button" onClick={() => setZoom((z) => Math.max(50, z - 25))} className="rounded px-2 py-1 hover:bg-slate-100 dark:hover:bg-slate-800">−</button>
                          {zoom}%
                          <button type="button" onClick={() => setZoom((z) => Math.min(250, z + 25))} className="rounded px-2 py-1 hover:bg-slate-100 dark:hover:bg-slate-800">+</button>
                          <button type="button" onClick={() => setZoom(100)} className="rounded px-2 py-1 hover:bg-slate-100 dark:hover:bg-slate-800">Fit</button>
                        </div>
                      )}
                      {doc.sample && <Link to={`/super-admin/onboarding/start?customer=${groupId}`} className="rounded-lg px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" title="Samples are uploaded and replaced in Step 0">Replace in Step 0</Link>}
                    </div>
                  </div>

                  {!doc.sample ? (
                    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed border-slate-300 p-12 text-center dark:border-slate-700">
                      <p className="text-base font-semibold text-slate-800 dark:text-slate-100">No sample {doc.name} yet</p>
                      <p className="text-sm text-slate-500">Samples are uploaded in Step 0, together with the ERP Excel.</p>
                      <Link to={`/super-admin/onboarding/start?customer=${groupId}`} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500">Upload it in Step 0</Link>
                    </div>
                  ) : (
                    <>
                      <p className="mb-2 text-xs text-slate-500">
                        {selectedField
                          ? <>Draw a box around <b>{fieldName(selectedField)}</b> on the page{selectedField.mark ? " - it replaces the current box" : ""}.</>
                          : "Pick a field on the right, then draw a box around its value."}
                      </p>
                      <div className="overflow-auto">
                        <div style={{ width: `${zoom}%`, minWidth: zoom > 100 ? `${zoom}%` : undefined }}>
                          <SampleCanvas
                            workbookId={data.workbook.id}
                            docId={doc.id}
                            page={page}
                            boxes={boxes}
                            canDraw={!!selectedField}
                            onDraw={(b) => void draw(b)}
                            onPick={(id) => setSelected(id)}
                          />
                        </div>
                      </div>
                    </>
                  )}
                </>
              )}
            </Card>

            {/* Fields of this document */}
            <Card className="h-fit p-4">
              <div className="mb-2 flex items-center justify-between">
                <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">Fields on {doc?.name}</p>
                {doc && <span className="text-xs text-slate-400">{countFor(doc.id).done} of {countFor(doc.id).total} marked</span>}
              </div>
              {docFields.length > 0 && (() => {
                const n = {
                  todo: docFields.filter((f) => !f.mark).length,
                  check: docFields.filter((f) => f.mark && (compare(f) === "differs" || compare(f) === "no-read")).length,
                  done: docFields.filter((f) => f.mark).length,
                  all: docFields.length,
                };
                const tabs: { key: typeof view; label: string; tone: string }[] = [
                  { key: "todo", label: "To mark", tone: "text-slate-700 dark:text-slate-200" },
                  { key: "check", label: "Check", tone: "text-amber-700 dark:text-amber-300" },
                  { key: "done", label: "Marked", tone: "text-emerald-700 dark:text-emerald-300" },
                  { key: "all", label: "All", tone: "text-slate-500" },
                ];
                return (
                  <>
                    <div className="mb-2 grid grid-cols-4 gap-1 rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
                      {tabs.map((t) => (
                        <button key={t.key} type="button" onClick={() => setView(t.key)}
                          title={t.key === "check" ? "Marked, but nothing was read inside the box or it differs from the Excel" : undefined}
                          className={`rounded-md px-1 py-1 text-xs font-medium ${view === t.key ? `bg-white shadow-sm dark:bg-slate-900 ${t.tone}` : "text-slate-500 hover:text-slate-700"}`}>
                          {t.label} <span className="opacity-70">{n[t.key]}</span>
                        </button>
                      ))}
                    </div>
                    <input value={fieldSearch} onChange={(e) => setFieldSearch(e.target.value)} placeholder="Search field…"
                      className="mb-2 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900" />
                  </>
                );
              })()}
              {docFields.length === 0 && (
                <p className="text-sm text-slate-500">No Document fields on this document. Put them on it in <Link className="text-indigo-600 underline" to={`/super-admin/onboarding/erp-fields?customer=${groupId}`}>Step 1</Link>.</p>
              )}
              <div className="flex max-h-[62vh] flex-col gap-1 overflow-y-auto pr-1">
                {docFields.length > 0 && shownFields.length === 0 && (
                  <p className="py-6 text-center text-sm text-slate-500">
                    {fieldSearch.trim() ? `No field matches "${fieldSearch}".`
                      : view === "todo" ? "✓ Every field on this document has a box."
                      : view === "check" ? "✓ Nothing to check - every box reads a value that fits the Excel."
                      : "No fields here yet."}
                  </p>
                )}
                {shownFields.map((f) => {
                  const res = compare(f);
                  const on = f.id === selected;
                  return (
                    <div
                      key={f.id}
                      onClick={() => { setSelected(on ? null : f.id); if (f.mark && f.mark.page !== page) setPage(f.mark.page); }}
                      title={f.samples.length ? `Excel: ${f.samples.join(" · ")}` : undefined}
                      className={`cursor-pointer rounded-md border px-2 py-1.5 text-sm ${on ? "border-indigo-500 ring-2 ring-indigo-200 dark:ring-indigo-500/30" : "border-slate-200 hover:border-slate-300 dark:border-slate-700"}`}
                    >
                      <div className="flex items-center gap-2">
                        {f.mark
                          ? <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] text-white" style={{ background: TONE[res ?? "no-excel"] }}>{res === "match" ? "✓" : res === "differs" || res === "no-read" ? "!" : "✓"}</span>
                          : <span className="h-3 w-3 shrink-0 rounded-full border-2 border-slate-300" />}
                        <span className="min-w-0 shrink truncate font-medium text-slate-800 dark:text-slate-100">{fieldName(f)}</span>
                        <span className="shrink-0 text-[10px] text-slate-400">{f.sheet_name}</span>
                        <span className="min-w-0 flex-1 truncate text-right font-mono text-[11px] text-slate-400">{f.samples[0] ?? ""}</span>
                      </div>
                      {f.mark && !on && (
                        <p className="mt-0.5 truncate pl-6 font-mono text-[11px]" style={{ color: TONE[res ?? "no-excel"] }}>
                          Read: {f.mark.detected_value || "nothing"} · p{f.mark.page}
                        </p>
                      )}
                      {f.mark && on && (
                        <div className="mt-1">
                          <p className="truncate font-mono text-[11px] text-slate-600 dark:text-slate-300">Read: {f.mark.detected_value || "—"}</p>
                          <p className="text-[11px] font-medium" style={{ color: TONE[res ?? "no-excel"] }}>
                            {res === "match" ? "✓ matches the Excel" : res === "differs" ? "⚠ differs from the Excel - check the box" : res === "no-read" ? "Nothing read inside the box" : "No Excel value to compare"}
                            {" · page "}{f.mark.page}
                          </p>
                          <div className="mt-1 flex items-center gap-3 text-[11px]" onClick={(e) => e.stopPropagation()}>
                            <label className="flex items-center gap-1 text-slate-500">
                              <input type="checkbox" checked={f.mark.line_item}
                                onChange={(e) => erpApi.setMarkLineItem(f.id, e.target.checked).then((mark) => replaceField(f.id, { mark }))
                                  .catch((err) => setMessage({ tone: "warn", text: errorText(err, "Could not save.") }))} />
                              line item (first row)
                            </label>
                            <button type="button" className="text-rose-500 hover:underline"
                              onClick={() => erpApi.clearMark(f.id).then(() => replaceField(f.id, { mark: null }))
                                .catch((err) => setMessage({ tone: "warn", text: errorText(err, "Could not clear.") }))}>
                              Clear box
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              <div className="mt-4 flex justify-end gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
                <Link to={`/super-admin/onboarding/erp-fields?customer=${groupId}`} className="rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800">← Step 1</Link>
                <Button size="sm" onClick={() => navigate(`/super-admin/onboarding/screen-designer?group=${groupId}`)}>
                  Next: Layout →
                </Button>
              </div>
            </Card>
          </div>
        )}
      </div>
    </AppShell>
  );
}
