import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import * as erpApi from "../../api/onboardingErp";
import type { DocMapping, ErpCustomer } from "../../api/onboardingErp";
import {
  OnboardingSteps, rememberOnboardingCustomer, useOnboardingCustomer, useOnboardingFullScreen,
} from "../../components/onboarding/OnboardingSteps";

// Customer Onboarding - Step 0: choose the customer template, then everything the later steps
// work from, in one place: the ERP Excel workbook and one sample per document. Steps 1-3 then
// carry the chosen customer without asking again.

function errorText(err: unknown, fallback: string): string {
  return (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
}

const DOC_ICON = (name: string) => {
  const n = name.toLowerCase();
  if (n.includes("lading") || n === "bl" || n.includes("waybill")) return "⚓";
  if (n.includes("packing") || n.includes("paackage") || n.includes("package")) return "📦";
  if (n.includes("invoice")) return "🧾";
  return "📄";
};

export function OnboardingStartPage() {
  const [, setParams] = useSearchParams();
  const navigate = useNavigate();
  const remembered = useOnboardingCustomer("customer");
  const [groupId, setGroupId] = useState(remembered);
  const [customers, setCustomers] = useState<ErpCustomer[]>([]);
  const [docs, setDocs] = useState<DocMapping | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [message, setMessage] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const [fullScreen, setFullScreen] = useOnboardingFullScreen();
  const excelInput = useRef<HTMLInputElement>(null);
  const sampleInput = useRef<HTMLInputElement>(null);
  const [sampleFor, setSampleFor] = useState<string>("");

  const loadCustomers = () => erpApi.listCustomers().then(setCustomers).catch(() => setMessage({ tone: "warn", text: "Could not load the templates." }));
  useEffect(() => { void loadCustomers(); }, []);

  const customer = customers.find((c) => c.group_id === groupId) ?? null;
  const workbookId = customer?.workbook?.id ?? "";

  useEffect(() => {
    if (!workbookId) { setDocs(null); return; }
    erpApi.getDocMapping(workbookId).then(setDocs).catch(() => setDocs(null));
  }, [workbookId]);

  useEffect(() => {
    if (!fullScreen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFullScreen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullScreen, setFullScreen]);

  const choose = (id: string) => {
    setGroupId(id);
    rememberOnboardingCustomer(id);
    setParams(new URLSearchParams({ customer: id }), { replace: true });
    setMessage(null);
  };

  const uploadExcel = async (file: File) => {
    if (!groupId) return;
    setBusy("excel");
    try {
      const w = await erpApi.uploadWorkbook(groupId, file);
      setMessage({ tone: "ok", text: `${w.file_name}: ${w.sheets.filter((x) => !x.erp_ui).length} worksheet(s), ${w.summary.total} column(s) read.` });
      await loadCustomers();
    } catch (err) {
      setMessage({ tone: "warn", text: errorText(err, "That workbook could not be read.") });
    } finally {
      setBusy(null);
      if (excelInput.current) excelInput.current.value = "";
    }
  };

  const uploadSample = async (file: File) => {
    if (!workbookId || !sampleFor) return;
    setBusy(sampleFor);
    try {
      setDocs(await erpApi.uploadSample(workbookId, sampleFor, file));
    } catch (err) {
      setMessage({ tone: "warn", text: errorText(err, "Could not read that file.") });
    } finally {
      setBusy(null);
      if (sampleInput.current) sampleInput.current.value = "";
    }
  };

  const templateSample = async (docId: string) => {
    if (!workbookId) return;
    setBusy(docId);
    try {
      setDocs(await erpApi.sampleFromTemplate(workbookId, docId));
    } catch (err) {
      setMessage({ tone: "warn", text: errorText(err, "Could not use the template's sample.") });
    } finally {
      setBusy(null);
    }
  };

  const LABEL: Record<string, string> = {
    document: "Document", reference: "Reference", fixed: "Fixed", operator: "Operator Input",
    ai: "AI-Derived", formula: "Formula / Rule",
  };
  const importSetup = async () => {
    if (!groupId || !customer) return;
    setBusy("import");
    const run = async (replace: boolean) => {
      const r = await erpApi.importCurrent(groupId, replace);
      const parts = Object.entries(r.by_category).map(([k, n]) => `${n} ${LABEL[k] ?? k}`).join(", ");
      setMessage({
        tone: "ok",
        text: `Imported from ${customer.name}: ${r.classified} columns classified (${parts}), ${r.left_unclassified} left unclassified, `
          + `${r.boxes} document boxes from the template's marks${r.samples_used ? `, ${r.samples_used} template sample(s) used` : ""}.`
          + (r.missing_fields.length ? ` ${r.missing_fields.length} column(s) point at fields the template does not have: ${r.missing_fields.join("; ")}.` : "")
          + (r.erp_ui_scripts.length
            ? ` ERP UI fields from ${r.erp_ui_scripts.join(", ")}: ${r.erp_ui_added.length ? r.erp_ui_added.join(", ") : "none new"}.`
            : " No ERP script is linked to this template - pick one on the ERP UI sheet in Step 1 to add the fields it selects on screen."),
      });
    };
    try {
      await run(false);
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 409 && window.confirm(`${errorText(err, "This customer already has classified fields.")}\n\nImport anyway and replace them?`)) {
        try { await run(true); } catch (e2) { setMessage({ tone: "warn", text: errorText(e2, "Could not import.") }); }
      } else if (status !== 409) {
        setMessage({ tone: "warn", text: errorText(err, "Could not import.") });
      }
    } finally {
      setBusy(null);
      await loadCustomers();
      const fresh = (await erpApi.listCustomers().catch(() => [])).find((c) => c.group_id === groupId);
      if (fresh?.workbook?.id) setDocs(await erpApi.getDocMapping(fresh.workbook.id).catch(() => null));
    }
  };

  const addDocument = async () => {
    if (!workbookId) return;
    const name = window.prompt("Document name (e.g. Bill of Lading, Certificate of Origin)");
    if (!name?.trim()) return;
    try {
      await erpApi.addDocument(workbookId, name.trim());
      setDocs(await erpApi.getDocMapping(workbookId));
    } catch (err) {
      setMessage({ tone: "warn", text: errorText(err, "Could not add the document.") });
    }
  };

  const q = search.trim().toLowerCase();
  const shown = customers.filter((c) => !q || `${c.name} ${c.tenant} ${c.mode ?? ""}`.toLowerCase().includes(q));
  const samplesDone = docs?.documents.filter((d) => d.sample).length ?? 0;

  return (
    <AppShell title="Choose Template" subtitle="Customer Onboarding · Step 0 - the customer template, its ERP Excel and sample documents">
      <div className={fullScreen ? "fixed inset-0 z-40 overflow-auto bg-slate-50 p-4 dark:bg-slate-950" : ""}>
        <OnboardingSteps current={0} groupId={groupId} />
        <input ref={excelInput} type="file" accept=".xlsx,.xlsm" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadExcel(f); }} />
        <input ref={sampleInput} type="file" accept=".pdf,.png,.jpg,.jpeg" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadSample(f); }} />

        {message && (
          <div className={`mb-4 rounded-lg border px-4 py-2 text-sm ${message.tone === "ok"
            ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200"
            : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"}`}>
            {message.text}
            <button type="button" className="ml-3 text-xs underline" onClick={() => setMessage(null)}>dismiss</button>
          </div>
        )}

        {/* 1. Template */}
        <Card className="mb-4 p-5">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <p className="text-base font-semibold text-slate-900 dark:text-slate-50">1. Choose the customer template to design</p>
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search template or customer…"
              className="ml-auto w-64 rounded-lg border border-slate-300 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900" />
            <Button size="sm" variant="ghost" onClick={() => setFullScreen((v) => !v)}>{fullScreen ? "✕ Close full screen" : "⛶ Full screen"}</Button>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {shown.map((c) => {
              const on = c.group_id === groupId;
              return (
                <button
                  key={c.group_id}
                  type="button"
                  onClick={() => choose(c.group_id)}
                  className={`rounded-xl border p-3 text-left transition-colors ${on
                    ? "border-indigo-500 bg-indigo-50 ring-2 ring-indigo-200 dark:bg-indigo-500/10 dark:ring-indigo-500/30"
                    : "border-slate-200 hover:border-slate-300 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"}`}
                >
                  <p className="font-semibold text-slate-900 dark:text-slate-50">{c.name}</p>
                  <p className="text-xs text-slate-500">{c.tenant}{c.mode ? ` · ${c.mode}` : ""}</p>
                  <p className="mt-2 text-xs">
                    {c.workbook
                      ? <span className="text-emerald-700 dark:text-emerald-300">ERP Excel ✓ · {c.workbook.percent}% classified{c.workbook.status === "complete" ? " · Step 1 done" : ""}</span>
                      : <span className="text-slate-400">No ERP Excel yet</span>}
                  </p>
                </button>
              );
            })}
            {shown.length === 0 && <p className="text-sm text-slate-500">No template matches "{search}".</p>}
          </div>
        </Card>

        {customer && (
          <div className="grid gap-4 lg:grid-cols-2">
            {/* 2. ERP Excel */}
            <Card className="h-fit p-5">
              <p className="text-base font-semibold text-slate-900 dark:text-slate-50">2. ERP Excel workbook</p>
              <p className="mb-3 text-xs text-slate-500">The customer's ERP import workbook - every worksheet and column is read from it for Step 1. The file itself is never changed.</p>
              {customer.excel_setup && (
                <div className="mb-3 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 dark:border-indigo-500/30 dark:bg-indigo-500/10">
                  <p className="text-sm font-medium text-indigo-800 dark:text-indigo-200">This template already runs on Excel entry</p>
                  <p className="mb-2 text-xs text-indigo-700/80 dark:text-indigo-200/80">
                    Import its current set-up: its ERP workbook, each column's category and document (Step 1), a box per document field from its marks (Step 2),
                    and fixed values, operator settings, AI prompts and formulas (Step 4). The template itself is only read, never changed.
                  </p>
                  <Button size="sm" isLoading={busy === "import"} onClick={() => void importSetup()}>Import current setup</Button>
                </div>
              )}
              {customer.workbook ? (
                <div className="flex flex-wrap items-center gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 dark:border-emerald-500/30 dark:bg-emerald-500/10">
                  <span className="rounded bg-emerald-600 px-1.5 text-xs font-bold text-white">X</span>
                  <span className="text-sm font-medium text-slate-800 dark:text-slate-100">{customer.workbook.file_name}</span>
                  <span className="text-xs text-slate-500">{customer.workbook.sheet_count} sheets · {customer.workbook.total} columns</span>
                  <Button size="sm" variant="secondary" className="ml-auto" isLoading={busy === "excel"} onClick={() => excelInput.current?.click()}>Replace</Button>
                </div>
              ) : (
                <Button isLoading={busy === "excel"} onClick={() => excelInput.current?.click()}>Upload ERP Excel (.xlsx)</Button>
              )}
              {customer.workbook && <p className="mt-2 text-[11px] text-slate-400">Replacing keeps every classification whose sheet and column still exist.</p>}
            </Card>

            {/* 3. Sample documents */}
            <Card className="h-fit p-5">
              <div className="flex items-center justify-between">
                <p className="text-base font-semibold text-slate-900 dark:text-slate-50">3. Sample documents</p>
                {docs && <span className="text-xs text-slate-400">{samplesDone} of {docs.documents.length} uploaded</span>}
              </div>
              <p className="mb-3 text-xs text-slate-500">One sample per document - Step 2 draws each field's box on it.</p>
              {!customer.workbook && <p className="text-sm text-slate-500">Upload the ERP Excel first - the documents come with it.</p>}
              {docs && (
                <div className="flex flex-col gap-2">
                  {docs.documents.map((d) => (
                    <div key={d.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
                      <span className="text-lg">{DOC_ICON(d.name)}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block font-medium text-slate-800 dark:text-slate-100">{d.name}</span>
                        <span className={`block text-xs ${d.sample ? "text-emerald-700 dark:text-emerald-300" : "text-slate-400"}`}>
                          {d.sample ? `✓ ${d.sample.file_name} · ${d.sample.page_count} page${d.sample.page_count === 1 ? "" : "s"}` : "no sample yet"}
                        </span>
                      </span>
                      {!d.sample && d.template_sample_available && (
                        <Button size="sm" variant="ghost" isLoading={busy === d.id} onClick={() => void templateSample(d.id)}>Use template's sample</Button>
                      )}
                      <Button size="sm" variant={d.sample ? "secondary" : "primary"} isLoading={busy === d.id}
                        onClick={() => { setSampleFor(d.id); sampleInput.current?.click(); }}>
                        {d.sample ? "Replace" : "Upload"}
                      </Button>
                    </div>
                  ))}
                  <button type="button" onClick={() => void addDocument()} className="self-start rounded-lg px-2 py-1 text-sm text-indigo-600 hover:bg-indigo-50 dark:text-indigo-300 dark:hover:bg-indigo-500/10">
                    + Add document
                  </button>
                  <p className="text-[11px] text-slate-400">Replacing a sample clears that document's boxes in Step 2.</p>
                </div>
              )}
            </Card>
          </div>
        )}

        {customer && (
          <div className="mt-4 flex justify-end">
            <Button disabled={!customer.workbook} onClick={() => navigate(`/super-admin/onboarding/erp-fields?customer=${groupId}`)}>
              Start: ERP Field Mapping →
            </Button>
          </div>
        )}
      </div>
    </AppShell>
  );
}
