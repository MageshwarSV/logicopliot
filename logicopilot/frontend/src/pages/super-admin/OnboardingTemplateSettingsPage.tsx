import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import {
  OnboardingCustomer, OnboardingSteps, useOnboardingCustomer, useOnboardingFullScreen,
} from "../../components/onboarding/OnboardingSteps";
import { TemplateFieldEditor, type TemplateFieldSection } from "../../components/onboarding/TemplateFieldEditor";
import * as onboardingApi from "../../api/onboarding";
import * as reviewsApi from "../../api/reviews";
import { listZipPasswords, addZipPassword, deleteZipPassword, type ZipPassword } from "../../api/zipPasswords";
import { connectMailbox, listMailboxes, updateMailbox, type ConnectedMailbox } from "../../api/emailPullerMailboxes";
import { createErpScript, listErpScripts, updateErpScript } from "../../api/erpScripts";
import { createUser } from "../../api/users";
import type { MailProvider } from "../../types/auth";
import type { ErpScript } from "../../types/erpScript";
import { CombinedDescriptionConfigModal } from "../jobs/CombinedDescriptionConfigModal";
import { setStatus as setOnboardingStatus } from "../../api/onboardingErp";
import { DOC_TYPES, MODES, type DemoResult, type ReferenceSheet } from "../../types/onboarding";
import {
  addTemplateDocument, apiError, createLiveCustomField, deleteClassifierSample, deleteTemplateDocument, getClassifierSamples, getLinks, getLiveGroup, getPromptHistory, getTemplateUsers,
  patchMark, renameDocument, setDocumentManualEntry, setDocumentOrder, setDocumentType, setImporterRequiresGstin,
  setPrealertShipments, setTemplateUser,
  type ClassifierSample, type FieldRef, type LiveGroup, type PromptHistoryRow, type SpecialName, type TemplateUser,
} from "../../api/onboardingTemplate";

// Customer Onboarding - Step 5: Template Settings. Everything that belongs to the template as a
// whole, on the LIVE template - the settings the old Document Templates, Customer List,
// Document Processing, Data Transformation, ERP Scripts, Email Puller, Zip Passwords and user
// screens hold (those screens stay exactly as they are).

const INPUT =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100";
const HELP = "text-[11px] leading-snug text-slate-500 dark:text-slate-400";

type SectionKey =
  | "documents" | "fields" | "rules" | "mail" | "processing" | "erp" | "reference" | "crossdoc"
  | "combined" | "users" | "samples" | "transform" | "names" | "test" | "history";

const SECTIONS: { key: SectionKey; label: string }[] = [
  { key: "documents", label: "Documents" },
  { key: "fields", label: "Template fields" },
  { key: "rules", label: "Template rules" },
  { key: "mail", label: "Mail intake & zips" },
  { key: "processing", label: "Processing" },
  { key: "erp", label: "ERP entry & scripts" },
  { key: "reference", label: "Reference data" },
  { key: "crossdoc", label: "Cross-document checks" },
  { key: "combined", label: "Combined Description" },
  { key: "users", label: "Users" },
  { key: "transform", label: "Data Transformation & approval" },
  { key: "test", label: "Test on samples" },
  { key: "samples", label: "Classifier samples" },
  { key: "history", label: "Prompt history" },
  { key: "names", label: "Built-in field names" },
];

function Toggle({ checked, onChange, label, help, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; help?: string; disabled?: boolean }) {
  return (
    <label className={`flex items-start gap-3 rounded-lg border border-slate-200 px-3 py-2.5 dark:border-slate-700 ${disabled ? "opacity-60" : "cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/60"}`}>
      <input type="checkbox" className="mt-0.5 h-4 w-4 accent-indigo-600" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>
        <span className="block text-sm font-medium text-slate-800 dark:text-slate-100">{label}</span>
        {help && <span className={HELP}>{help}</span>}
      </span>
    </label>
  );
}

function Section({ title, help, children, actions }: { title: string; help?: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <Card className="p-5">
      <div className="mb-4 flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-50">{title}</h2>
          {help && <p className={`mt-0.5 ${HELP}`}>{help}</p>}
        </div>
        {actions}
      </div>
      <div className="flex flex-col gap-3">{children}</div>
    </Card>
  );
}

export function OnboardingTemplateSettingsPage() {
  const groupId = useOnboardingCustomer("customer");
  const [group, setGroup] = useState<LiveGroup | null>(null);
  const [section, setSection] = useState<SectionKey>("documents");
  const [message, setMessage] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const [fullScreen, setFullScreen] = useOnboardingFullScreen();
  const [edit, setEdit] = useState<{ ref: FieldRef; section?: TemplateFieldSection } | null>(null);
  const openField: OpenField = (ref, section) => setEdit({ ref, section });

  async function reload() {
    if (!groupId) return;
    try { setGroup(await getLiveGroup(groupId)); } catch (e) { setMessage({ tone: "warn", text: apiError(e, "Could not load the template.") }); }
  }
  useEffect(() => { void reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [groupId]);

  useEffect(() => {
    if (!fullScreen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFullScreen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullScreen, setFullScreen]);

  /** Run a live-template change, then reload and say what happened. */
  async function act(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      await reload();
      setMessage({ tone: "ok", text: ok });
    } catch (e) {
      setMessage({ tone: "warn", text: apiError(e, "Could not save.") });
    }
  }

  return (
    <AppShell title="Template Settings" subtitle="Customer Onboarding · Step 5 - everything that belongs to the template as a whole, on the live template">
      <div className={fullScreen ? "fixed inset-0 z-40 overflow-auto bg-slate-50 p-4 dark:bg-slate-950" : ""}>
        <OnboardingSteps current={5} groupId={groupId} />
        <Card className="mb-4 flex flex-wrap items-center gap-3 p-4">
          <OnboardingCustomer name={group?.name} groupId={groupId} />
          {group && (
            <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
              group.status === "approved" ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300"
                : group.status === "changes_requested" ? "bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300"
                : "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300"}`}>
              {group.status === "approved" ? "● Live - operators can use it" : group.status === "changes_requested" ? "Changes requested - not live" : "In testing - not live yet"}
            </span>
          )}
          <div className="ml-auto">
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
        {groupId && !group && <Card className="p-10 text-center text-sm text-slate-500">Loading…</Card>}

        {group && <GoLiveCard group={group} act={act} />}

        {group && (
          <div className="grid gap-4 lg:grid-cols-[15rem_minmax(0,1fr)]">
            <Card className="h-fit p-2">
              <nav className="flex flex-col gap-0.5">
                {SECTIONS.map((s) => (
                  <button key={s.key} type="button" onClick={() => setSection(s.key)}
                    className={`rounded-lg px-3 py-2 text-left text-sm ${section === s.key ? "bg-indigo-600 font-semibold text-white" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
                    {s.label}
                  </button>
                ))}
              </nav>
            </Card>

            <div className="flex min-w-0 flex-col gap-4">
              {section === "documents" && <DocumentsSection group={group} act={act} />}
              {section === "fields" && <FieldsSection group={group} act={act} openField={openField} />}
              {section === "rules" && <RulesSection group={group} act={act} />}
              {section === "mail" && <MailSection group={group} act={act} />}
              {section === "processing" && <ProcessingSection group={group} act={act} />}
              {section === "erp" && <ErpSection group={group} act={act} />}
              {section === "reference" && <ReferenceDataSection group={group} act={act} openField={openField} />}
              {section === "crossdoc" && <CrossDocSection group={group} act={act} openField={openField} />}
              {section === "combined" && <CombinedSection group={group} act={act} openField={openField} />}
              {section === "users" && <UsersSection group={group} act={act} />}
              {section === "transform" && <TransformSection group={group} act={act} openField={openField} />}
              {section === "test" && <TestSection group={group} />}
              {section === "samples" && <SamplesSection group={group} />}
              {section === "history" && <HistoryTable group={group} />}
              {section === "names" && <NamesSection group={group} />}
            </div>
          </div>
        )}

        {group && (
          <div className="mt-4 flex justify-end gap-2">
            <Link to={`/super-admin/onboarding/field-config?customer=${groupId}`} className="rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800">← Step 4</Link>
          </div>
        )}
      </div>
      {group && (
        <TemplateFieldEditor groupId={group.id} fieldRef={edit?.ref ?? null} open={!!edit} initialSection={edit?.section}
          onClose={() => setEdit(null)} onSaved={() => void reload()} />
      )}
    </AppShell>
  );
}

type Act = (fn: () => Promise<unknown>, ok: string) => Promise<void>;
type OpenField = (ref: FieldRef, section?: TemplateFieldSection) => void;

// ---- Finish setup: make the template live ------------------------------------------------------------
//
// "Live" = status approved: the only status operators see in New Job (jobs.available_groups).
// Before that, a checklist: blocking problems stop it, warnings are shown but allowed.

type Check = { ok: boolean; level: "block" | "warn"; text: string; fix?: string };

function GoLiveCard({ group, act }: { group: LiveGroup; act: Act }) {
  const [open, setOpen] = useState(false);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [workbookId, setWorkbookId] = useState<string | null>(null);
  const live = group.status === "approved";

  async function runChecks() {
    setChecks(null);
    setOpen(true);
    const out: Check[] = [];
    const docs = group.documents;
    const required = docs.filter((d) => d.is_required !== false);
    const boxes = (d: (typeof docs)[number]) => d.marks.filter((m) => m.width > 0).length;

    out.push({ ok: docs.length > 0, level: "block", text: docs.length ? `${docs.length} document(s) set up` : "The template has no documents", fix: "Step 0 / Template Settings → Documents" });
    const noSample = docs.filter((d) => !d.is_uploaded);
    out.push({ ok: !noSample.length, level: "warn", text: noSample.length ? `No sample yet: ${noSample.map((d) => d.name).join(", ")}` : "Every document has a sample", fix: "Template Settings → Documents → Upload sample" });
    const noBox = required.filter((d) => boxes(d) === 0);
    out.push({ ok: !noBox.length, level: "block", text: noBox.length ? `Required document(s) with no box drawn: ${noBox.map((d) => d.name).join(", ")}` : "Every required document has its boxes", fix: "Step 2 Document Mapping" });

    if (group.entry_mode === "excel") {
      const sheets = ((group.excel_config as { sheets?: unknown[] } | null)?.sheets ?? []).length
        || ((group.excel_config as { columns?: unknown[] } | null)?.columns?.length ? 1 : 0);
      out.push({ ok: sheets > 0, level: "block", text: sheets ? `ERP Excel mapping: ${sheets} sheet(s)` : "ERP Excel import is on but no sheet is mapped", fix: "Step 1 → Live template → ERP Excel column rules" });
    }
    try {
      const l = await getLinks(group.id);
      setWorkbookId(l.workbook_id);
      const loose = l.fields.filter((f) => f.category && !f.ref);
      out.push({ ok: !loose.length, level: "warn",
        text: loose.length ? `${loose.length} classified onboarding field(s) not connected to the live template (they will not be filled): ${loose.slice(0, 6).map((f) => f.excel_name).join(", ")}${loose.length > 6 ? "…" : ""}` : "Every classified field is connected to the live template",
        fix: "Step 1 / Step 4 → Live template → Link or Create" });
    } catch { /* no onboarding workbook - nothing to check */ }
    try {
      const scripts = await listErpScripts(group.tenant_id);
      const ready = scripts.find((s) => (s.template_ids ?? []).includes(group.id) && s.status === "ready");
      out.push({ ok: !!ready, level: "warn", text: ready ? `ERP script ready: ${ready.name}` : "No ready ERP script linked - jobs can be extracted but not sent to the ERP", fix: "Template Settings → ERP entry & scripts" });
    } catch { /* scripts unreadable - skip */ }
    const noFormat = docs.flatMap((d) => d.marks).filter((m) => m.width > 0 && m.ask_operator && !m.ask_operator_hint).length;
    if (noFormat) out.push({ ok: false, level: "warn", text: `${noFormat} operator question(s) have no hint for the operator`, fix: "Field settings → Operator" });
    setChecks(out);
  }

  const blocked = (checks ?? []).some((c) => !c.ok && c.level === "block");

  async function goLive() {
    await act(async () => {
      if (workbookId) { try { await erpApiSetComplete(workbookId); } catch { /* onboarding status is informational */ } }
      await reviewsApi.approveTemplate(group.id);
    }, `"${group.name}" is live - operators can now pick it in New Job.`);
    setOpen(false);
  }

  return (
    <>
      <Card className={`mb-4 flex flex-wrap items-center gap-4 p-5 ${live ? "border-emerald-300 bg-emerald-50/60 dark:border-emerald-500/30 dark:bg-emerald-500/5" : "border-indigo-300 bg-indigo-50/50 dark:border-indigo-500/30 dark:bg-indigo-500/5"}`}>
        <div className="min-w-0 flex-1">
          <p className="text-base font-semibold text-slate-900 dark:text-slate-50">
            {live ? "This template is live" : "Finish setup and make this template live"}
          </p>
          <p className={HELP}>
            {live
              ? "Operators can pick it in New Job and every mail / upload for it runs with these settings."
              : "Until then only Super Admins can test it - operators do not see it in New Job. A quick check runs first."}
          </p>
        </div>
        {live ? (
          <Button variant="secondary" onClick={() => window.confirm(`Take "${group.name}" off live? Operators will no longer see it in New Job; jobs already created are not affected.`)
            && act(() => onboardingApi.finalizeGroup(group.id), `"${group.name}" is back in testing - operators no longer see it.`)}>
            Take off live (back to testing)
          </Button>
        ) : (
          <Button onClick={() => void runChecks()}>✓ Finish setup — make template live</Button>
        )}
      </Card>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4" onClick={() => setOpen(false)}>
          <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl dark:bg-slate-900" onClick={(e) => e.stopPropagation()}>
            <p className="text-lg font-semibold text-slate-900 dark:text-slate-50">Make "{group.name}" live?</p>
            <p className={`mb-4 ${HELP}`}>Operators will see it in New Job and its mails / uploads run with these settings.</p>
            {!checks && <p className="py-6 text-center text-sm text-slate-500">Checking the template…</p>}
            {checks && (
              <ul className="flex flex-col gap-2">
                {checks.map((c, i) => (
                  <li key={i} className={`rounded-lg border px-3 py-2 text-sm ${c.ok ? "border-emerald-200 bg-emerald-50 dark:border-emerald-500/30 dark:bg-emerald-500/10"
                    : c.level === "block" ? "border-rose-200 bg-rose-50 dark:border-rose-500/30 dark:bg-rose-500/10" : "border-amber-200 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-500/10"}`}>
                    <span className="font-medium">{c.ok ? "✓" : c.level === "block" ? "✗" : "⚠"} {c.text}</span>
                    {!c.ok && c.fix && <span className={`block ${HELP}`}>Fix in: {c.fix}</span>}
                  </li>
                ))}
              </ul>
            )}
            {checks && blocked && <p className="mt-3 text-sm font-medium text-rose-700 dark:text-rose-300">Fix the ✗ items first - the template cannot go live with them.</p>}
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setOpen(false)}>Not yet</Button>
              <Button disabled={!checks || blocked} onClick={() => void goLive()}>
                {checks && checks.some((c) => !c.ok) && !blocked ? "Make live anyway" : "Make live now"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

async function erpApiSetComplete(workbookId: string) {
  await setOnboardingStatus(workbookId, "complete");
}

// ---- Documents -------------------------------------------------------------------------------------

function DocumentsSection({ group, act }: { group: LiveGroup; act: Act }) {
  const docs = [...group.documents].sort((a, b) => a.order_index - b.order_index);
  const [add, setAdd] = useState({ name: "", type: "Custom", required: true });
  return (
    <Section title="Documents" help="Name, type, whether a job needs it, manual entry when it is missing, its order, and its sample.">
      <div className="grid gap-2 rounded-xl border border-dashed border-indigo-300 bg-indigo-50/40 p-3 dark:border-indigo-500/30 dark:bg-indigo-500/5 sm:grid-cols-[1fr_10rem_auto_auto]">
        <input className={INPUT} placeholder="New document name, e.g. Certificate of Origin" value={add.name} onChange={(e) => setAdd({ ...add, name: e.target.value })} />
        <select className={INPUT} value={add.type} onChange={(e) => setAdd({ ...add, type: e.target.value })}>
          {DOC_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="accent-indigo-600" checked={add.required} onChange={(e) => setAdd({ ...add, required: e.target.checked })} /> Required
        </label>
        <Button size="sm" disabled={!add.name.trim()} onClick={() => act(
          () => addTemplateDocument(group.id, { name: add.name.trim(), doc_type: add.type, is_required: add.required })
            .then(() => setAdd({ name: "", type: "Custom", required: true })),
          `Added the document ${add.name.trim()} - upload its sample, then draw its boxes in Step 2.`)}>+ Add document</Button>
      </div>
      {docs.map((d, i) => (
        <div key={d.id} className="grid items-center gap-2 rounded-xl border border-slate-200 p-3 dark:border-slate-700 md:grid-cols-[auto_1fr_10rem_auto]">
          <div className="flex flex-col">
            <button type="button" className="text-xs text-slate-400 hover:text-indigo-600 disabled:opacity-30" disabled={i === 0}
              onClick={() => act(() => setDocumentOrder(d.id, i - 1), `Moved ${d.name} up.`)}>▲</button>
            <button type="button" className="text-xs text-slate-400 hover:text-indigo-600 disabled:opacity-30" disabled={i === docs.length - 1}
              onClick={() => act(() => setDocumentOrder(d.id, i + 1), `Moved ${d.name} down.`)}>▼</button>
          </div>
          <input className={INPUT} defaultValue={d.name}
            onBlur={(e) => { const n = e.target.value.trim(); if (n && n !== d.name) void act(() => renameDocument(d.id, n), `Renamed to ${n}.`); }} />
          <select className={INPUT} value={d.doc_type} onChange={(e) => act(() => setDocumentType(d.id, e.target.value), `${d.name}: type ${e.target.value}.`)}>
            {[...new Set([...DOC_TYPES, d.doc_type])].map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <label className="cursor-pointer rounded-lg border border-slate-200 px-3 py-2 text-xs hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800">
            {d.is_uploaded ? `Sample · ${d.page_count} page(s) · replace` : "Upload sample"}
            <input type="file" accept=".pdf,.png,.jpg,.jpeg" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void act(() => onboardingApi.uploadDocument(d.id, f), `${d.name}: sample uploaded.`); }} />
          </label>
          <div className="grid gap-2 sm:grid-cols-2 md:col-span-4">
            <Toggle checked={d.is_required !== false} label="Required - a job waits for it"
              onChange={(v) => act(() => onboardingApi.setDocumentRequired(d.id, v), `${d.name}: ${v ? "required" : "optional"}.`)} />
            <Toggle checked={Boolean(d.manual_entry_when_missing)} label="Operator types its fields when it is missing"
              onChange={(v) => act(() => setDocumentManualEntry(d.id, v), `${d.name}: manual entry ${v ? "on" : "off"}.`)} />
          </div>
          <div className="flex items-center gap-2 md:col-span-4">
            <p className={HELP}>{d.marks.filter((m) => m.width > 0).length} box(es) drawn. Draw / redraw boxes in Step 2.</p>
            <button type="button" className="ml-auto text-xs text-rose-600 hover:underline"
              onClick={() => window.confirm(`Delete the document "${d.name}" and its ${d.marks.length} box(es) from the template? Only possible while no job uses it.`)
                && act(() => deleteTemplateDocument(d.id), `Deleted ${d.name}.`)}>
              Delete document
            </button>
          </div>
        </div>
      ))}
    </Section>
  );
}

// ---- Template fields ----------------------------------------------------------------------------------

const NEW_FIELD_KINDS: { key: string; label: string; payload: Record<string, unknown> }[] = [
  { key: "operator", label: "The operator answers it", payload: { kind: "hardcoded", ask_operator: true } },
  { key: "fixed", label: "A fixed value", payload: { kind: "hardcoded" } },
  { key: "ai", label: "The AI works it out", payload: { kind: "ai" } },
  { key: "reference", label: "Looked up in its own table", payload: { kind: "hardcoded", is_target_value: true, fuzzy_match: true } },
  { key: "lookup", label: "Looked up in the material master (per line)", payload: { kind: "lookup", per_row: true } },
  { key: "composite", label: "Joined from other fields (per line)", payload: { kind: "composite", per_row: true } },
];

async function createField(group: LiveGroup, name: string, kindKey: string): Promise<string> {
  const kind = NEW_FIELD_KINDS.find((k) => k.key === kindKey) ?? NEW_FIELD_KINDS[0];
  const cf = await createLiveCustomField(group.id, { label_name: name.trim(), ...kind.payload });
  return cf.id;
}

function FieldsSection({ group, act, openField }: { group: LiveGroup; act: Act; openField: OpenField }) {
  const [q, setQ] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState("operator");
  const rows = [
    ...group.documents.flatMap((d) => d.marks.filter((m) => m.width > 0).map((m) => ({
      ref: `m:${m.id}`, label: m.label_name, shown: m.display_label, where: `Box on ${d.name}`,
      flags: [m.ask_operator && "asks operator", m.is_target_value && "lookup", m.is_multi_value && "per row"].filter(Boolean) as string[],
    }))),
    ...(group.custom_fields ?? []).map((c) => ({
      ref: `c:${c.id}`, label: c.label_name, shown: c.display_label,
      where: c.kind === "ai" ? "AI works it out" : c.kind === "lookup" ? "Material master lookup" : c.kind === "composite" ? "Joined from fields" : "Fixed / operator",
      flags: [c.ask_operator && "asks operator", c.is_target_value && "lookup", c.per_row && "per line", c.hidden_from_capture && "hidden"].filter(Boolean) as string[],
    })),
  ].filter((r) => !q || `${r.label} ${r.shown ?? ""} ${r.where}`.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => a.label.localeCompare(b.label));

  return (
    <Section title="Template fields" help="Every field of the live template - boxes drawn on documents and fields worked out another way. Open one for all its settings, or add a field that is not an ERP Excel column (e.g. an operator question)."
      actions={<input className={`${INPUT} w-56`} placeholder="Search fields" value={q} onChange={(e) => setQ(e.target.value)} />}>
      <div className="grid gap-2 rounded-xl border border-dashed border-indigo-300 bg-indigo-50/40 p-3 dark:border-indigo-500/30 dark:bg-indigo-500/5 sm:grid-cols-[1fr_16rem_auto]">
        <input className={INPUT} placeholder="New field name, e.g. Duty Payment Method" value={name} onChange={(e) => setName(e.target.value)} />
        <select className={INPUT} value={kind} onChange={(e) => setKind(e.target.value)}>
          {NEW_FIELD_KINDS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
        </select>
        <Button size="sm" disabled={!name.trim()} onClick={() => act(async () => {
          const id = await createField(group, name, kind);
          setName("");
          openField(`c:${id}`, kind === "ai" || kind === "lookup" || kind === "composite" ? "kind" : kind === "reference" ? "reference" : "name");
        }, `Created the field ${name.trim()} - finish its settings in the popup.`)}>+ New field</Button>
      </div>
      <p className={HELP}>A box on a document is created by drawing it in Step 2.</p>
      <div className="flex max-h-[34rem] flex-col gap-1 overflow-y-auto">
        {rows.map((r) => (
          <div key={r.ref} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-700">
            <span className="min-w-0 flex-1 truncate">
              <b>{r.shown || r.label}</b>{r.shown && <span className={`ml-1 ${HELP}`}>({r.label})</span>}
              <span className={`ml-2 ${HELP}`}>{r.where}{r.flags.length ? ` · ${r.flags.join(" · ")}` : ""}</span>
            </span>
            <Button size="sm" variant="secondary" onClick={() => openField(r.ref)}>Open</Button>
          </div>
        ))}
        {!rows.length && <p className={HELP}>No fields{q ? ` match "${q}"` : " yet"}.</p>}
      </div>
    </Section>
  );
}

/** A one-line "create a field of this kind" form, used where a section needs a new field. */
function NewFieldRow({ group, act, openField, kinds, placeholder }: {
  group: LiveGroup; act: Act; openField: OpenField; kinds: string[]; placeholder: string;
}) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState(kinds[0]);
  const options = NEW_FIELD_KINDS.filter((k) => kinds.includes(k.key));
  return (
    <div className={`grid gap-2 rounded-xl border border-dashed border-indigo-300 bg-indigo-50/40 p-3 dark:border-indigo-500/30 dark:bg-indigo-500/5 ${options.length > 1 ? "sm:grid-cols-[1fr_16rem_auto]" : "sm:grid-cols-[1fr_auto]"}`}>
      <input className={INPUT} placeholder={placeholder} value={name} onChange={(e) => setName(e.target.value)} />
      {options.length > 1 && (
        <select className={INPUT} value={kind} onChange={(e) => setKind(e.target.value)}>
          {options.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
        </select>
      )}
      <Button size="sm" disabled={!name.trim()} onClick={() => act(async () => {
        const id = await createField(group, name, kind);
        setName("");
        openField(`c:${id}`, kind === "reference" ? "reference" : "kind");
      }, `Created ${name.trim()} - finish its settings in the popup.`)}>+ Create</Button>
    </div>
  );
}

// ---- Template rules ---------------------------------------------------------------------------------

function RulesSection({ group, act }: { group: LiveGroup; act: Act }) {
  const [ruling, setRulingText] = useState(group.ruling_prompt ?? "");
  return (
    <Section title="Template rules">
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">Transport mode</span>
        <select className={INPUT} value={group.mode ?? ""} onChange={(e) => act(() => onboardingApi.setGroupMode(group.id, e.target.value), `Mode: ${e.target.value}.`)}>
          <option value="">—</option>
          {MODES.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </label>
      <Toggle checked={Boolean(group.importer_requires_gstin)} label="Importer must have a GSTIN"
        help="A branch whose address has no GSTIN is left empty for the operator to pick."
        onChange={(v) => act(() => setImporterRequiresGstin(group.id, v), `GSTIN rule ${v ? "on" : "off"}.`)} />
      <Toggle checked={Boolean(group.prealert_shipments)} label="Pre-alert shipments"
        help="Pre-alert mails create shipments for this template."
        onChange={(v) => act(() => setPrealertShipments(group.id, v), `Pre-alert ${v ? "on" : "off"}.`)} />
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">Ruling (cross-document rules for the whole job)</span>
        <textarea className={`${INPUT} font-mono text-xs`} rows={8} value={ruling} onChange={(e) => setRulingText(e.target.value)} />
      </label>
      <div><Button size="sm" onClick={() => act(() => onboardingApi.setRuling(group.id, ruling.trim() || null), "Ruling saved.")}>Save ruling</Button></div>
    </Section>
  );
}

// ---- Mail intake & zips -------------------------------------------------------------------------------

function MailSection({ group, act }: { group: LiveGroup; act: Act }) {
  const [email, setEmail] = useState(group.pull_email ?? "");
  const [operator, setOperator] = useState(group.pull_operator_id ?? "");
  const [users, setUsers] = useState<TemplateUser[]>([]);
  const [boxes, setBoxes] = useState<ConnectedMailbox[]>([]);
  const [zips, setZips] = useState<ZipPassword[]>([]);
  const [zip, setZip] = useState({ label: "", password: "", domains: "" });
  const [newBox, setNewBox] = useState<{ provider: MailProvider; email: string; password: string }>({ provider: "gmail", email: "", password: "" });

  async function load() {
    const [u, b, z] = await Promise.all([getTemplateUsers(group.id), listMailboxes(), listZipPasswords()]);
    setUsers(u); setBoxes(b); setZips(z);
  }
  useEffect(() => { void load().catch(() => undefined); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [group.id]);

  const mine = boxes.filter((b) => b.group_id === group.id);
  const available = boxes.filter((b) => b.group_id !== group.id && (!b.tenant_id || b.tenant_id === group.tenant_id));

  return (
    <>
      <Section title="Mail intake" help="Mails to this address become jobs of this template, created for this operator.">
        <div className="grid gap-2 sm:grid-cols-2">
          <input className={INPUT} placeholder="e.g. cdcimports@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          <select className={INPUT} value={operator} onChange={(e) => setOperator(e.target.value)}>
            <option value="">— operator —</option>
            {users.filter((u) => u.role === "operator" || u.role === "tenant_admin").map((u) => <option key={u.id} value={u.id}>{u.name} · {u.email}</option>)}
          </select>
        </div>
        {!users.some((u) => u.role === "operator" || u.role === "tenant_admin") && (
          <p className={HELP}>This customer has no operator or Tenant Admin login yet - create one in Users (left menu), then pick it here.</p>
        )}
        <div><Button size="sm" onClick={() => act(() => onboardingApi.setPullEmail(group.id, email.trim() || null, operator || null), "Mail intake saved.")}>Save mail intake</Button></div>
      </Section>

      <Section title="Connected mailboxes" help="A mailbox routed to this template sends every message straight here. Connect new mailboxes on the Email Puller page.">
        {mine.length === 0 && <p className={HELP}>No mailbox routed to this template.</p>}
        {mine.map((b) => (
          <div key={b.id} className="flex items-center gap-2 text-sm">
            <span className="font-medium">{b.email}</span><span className={HELP}>{b.provider}{b.paused ? " · paused" : ""}</span>
            <Button size="sm" variant="secondary" className="ml-auto" onClick={() => act(() => updateMailbox(b.id, { group_id: null }).then(load), `${b.email} no longer routed here.`)}>Unroute</Button>
          </div>
        ))}
        {available.length > 0 && (
          <select className={INPUT} value="" onChange={(e) => {
            const b = boxes.find((x) => x.id === e.target.value);
            if (b) void act(() => updateMailbox(b.id, { tenant_id: group.tenant_id, group_id: group.id }).then(load), `${b.email} now routed here.`);
          }}>
            <option value="">+ Route a mailbox to this template</option>
            {available.map((b) => <option key={b.id} value={b.id}>{b.email}{b.group_name ? ` (now: ${b.group_name})` : ""}</option>)}
          </select>
        )}
        <div className="grid gap-2 rounded-xl border border-dashed border-indigo-300 bg-indigo-50/40 p-3 dark:border-indigo-500/30 dark:bg-indigo-500/5 sm:grid-cols-[8rem_1fr_1fr_auto]">
          <select className={INPUT} value={newBox.provider} onChange={(e) => setNewBox({ ...newBox, provider: e.target.value as MailProvider })}>
            <option value="gmail">Gmail</option>
            <option value="zoho">Zoho</option>
          </select>
          <input className={INPUT} placeholder="Mailbox email" value={newBox.email} onChange={(e) => setNewBox({ ...newBox, email: e.target.value })} />
          <input className={INPUT} placeholder="App password" type="password" autoComplete="new-password" value={newBox.password} onChange={(e) => setNewBox({ ...newBox, password: e.target.value })} />
          <Button size="sm" disabled={!newBox.email.trim() || !newBox.password} onClick={() => act(async () => {
            const mb = await connectMailbox({ email: newBox.email.trim(), app_password: newBox.password, provider: newBox.provider });
            await updateMailbox(mb.id, { tenant_id: group.tenant_id, group_id: group.id });
            setNewBox({ provider: newBox.provider, email: "", password: "" });
            await load();
          }, "Mailbox connected and routed to this template.")}>+ Connect mailbox</Button>
          <p className={`${HELP} sm:col-span-4`}>Uses the mailbox's app password (not the normal login password). The app checks it can log in before saving.</p>
        </div>
      </Section>

      <Section title="Zip passwords" help="Used to open password-protected zip attachments (e.g. Flex).">
        {zips.filter((z) => z.group_id === group.id).map((z) => (
          <div key={z.id} className="flex items-center gap-2 text-sm">
            <span className="font-medium">{z.label}</span>
            <span className={HELP}>{z.sender_domains?.length ? z.sender_domains.join(", ") : "any sender"} · used {z.use_count}×</span>
            <button type="button" className="ml-auto text-slate-400 hover:text-rose-600"
              onClick={() => window.confirm(`Delete the zip password "${z.label}"?`) && act(() => deleteZipPassword(z.id).then(load), "Zip password deleted.")}>✕</button>
          </div>
        ))}
        <div className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_auto]">
          <input className={INPUT} placeholder="Label" value={zip.label} onChange={(e) => setZip({ ...zip, label: e.target.value })} />
          <input className={INPUT} placeholder="Password" type="password" autoComplete="new-password" value={zip.password} onChange={(e) => setZip({ ...zip, password: e.target.value })} />
          <input className={INPUT} placeholder="Sender domains (optional, comma)" value={zip.domains} onChange={(e) => setZip({ ...zip, domains: e.target.value })} />
          <Button size="sm" disabled={!zip.label.trim() || !zip.password} onClick={() => act(() => addZipPassword({
            label: zip.label.trim(), password: zip.password, group_id: group.id,
            sender_domains: zip.domains.split(",").map((s) => s.trim()).filter(Boolean) || null,
          }).then(() => { setZip({ label: "", password: "", domains: "" }); return load(); }), "Zip password added.")}>Add</Button>
        </div>
      </Section>
    </>
  );
}

// ---- Processing ----------------------------------------------------------------------------------------

function ProcessingSection({ group, act }: { group: LiveGroup; act: Act }) {
  const [mb, setMb] = useState(String(group.large_zip_mb ?? 5));
  const [ignore, setIgnore] = useState((group.excel_ignore_patterns ?? []).join("\n"));
  const save = (body: Parameters<typeof onboardingApi.setProcessingSettings>[1], ok: string) =>
    act(() => onboardingApi.setProcessingSettings(group.id, body), ok);
  return (
    <Section title="Processing" help="When extraction starts and how big files and Excel attachments are handled.">
      <Toggle checked={group.auto_extract !== false} label="Extract automatically when documents arrive" onChange={(v) => save({ auto_extract: v }, `Auto-extract ${v ? "on" : "off"}.`)} />
      <Toggle checked={Boolean(group.large_zip_hold)} label="Hold large zips for a person to start" onChange={(v) => save({ large_zip_hold: v }, `Large zip hold ${v ? "on" : "off"}.`)} />
      {group.large_zip_hold && (
        <div className="flex items-center gap-2">
          <span className="text-sm">Hold zips larger than</span>
          <input className={`${INPUT} w-24`} inputMode="numeric" value={mb} onChange={(e) => setMb(e.target.value.replace(/\D/g, ""))} />
          <span className="text-sm">MB</span>
          <Button size="sm" variant="secondary" onClick={() => save({ large_zip_mb: Math.max(1, Number(mb) || 5) }, "Size saved.")}>Save</Button>
        </div>
      )}
      <Toggle checked={Boolean(group.large_job_lane)} label="Send big jobs to the large-job lane" onChange={(v) => save({ large_job_lane: v }, `Large-job lane ${v ? "on" : "off"}.`)} />
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">Excel attachments</span>
        <select className={INPUT} value={group.excel_mode ?? "legacy"} onChange={(e) => save({ excel_mode: e.target.value }, "Excel mode saved.")}>
          <option value="legacy">Converted to PDF (legacy)</option>
          <option value="sheet">Each sheet read from its cells</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">Excel file names that are never documents (one per line)</span>
        <textarea className={`${INPUT} font-mono text-xs`} rows={4} value={ignore} onChange={(e) => setIgnore(e.target.value)} />
      </label>
      <div><Button size="sm" onClick={() => save({ excel_ignore_patterns: ignore.split("\n").map((s) => s.trim()).filter(Boolean) }, "Ignore list saved.")}>Save ignore list</Button></div>
    </Section>
  );
}

// ---- ERP entry & scripts -------------------------------------------------------------------------------

function ErpSection({ group, act }: { group: LiveGroup; act: Act }) {
  const [scripts, setScripts] = useState<ErpScript[]>([]);
  const [branch, setBranch] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState({ name: "", url: "", login: true, username: "", password: "", branchField: "" });
  async function load() {
    const s = await listErpScripts(group.tenant_id);
    setScripts(s);
    setBranch(Object.fromEntries(s.map((x) => [x.id, x.branch_field ?? ""])));
  }
  useEffect(() => { void load().catch(() => undefined); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [group.id]);
  const linked = scripts.filter((s) => (s.template_ids ?? []).includes(group.id));
  const ready = linked.find((s) => s.status === "ready");
  const cfg = group.excel_config as { file_name?: string; sheets?: unknown[] } | null | undefined;
  const isExcel = group.entry_mode === "excel";

  async function createScript() {
    const created = await createErpScript(group.tenant_id, {
      name: draft.name.trim(), url: draft.url.trim(), has_login: draft.login,
      login_username: draft.login ? draft.username.trim() || null : null,
      login_password: draft.login ? draft.password || null : null,
      template_ids: [group.id],
    });
    if (draft.branchField.trim()) await updateErpScript(created.id, { branch_field: draft.branchField.trim() });
    setDraft({ name: "", url: "", login: true, username: "", password: "", branchField: "" });
    await load();
    window.open(`/super-admin/erp-scripts/${created.id}`, "_blank", "noopener");
  }

  return (
    <>
      <Section title="How jobs reach the ERP">
        <div className="grid gap-2 sm:grid-cols-2">
          {[
            { mode: "excel", title: "ERP Excel import", text: "The job becomes one Excel workbook that the ERP script uploads." },
            { mode: "fields", title: "Field by field (web entry)", text: "The ERP script types every value into the ERP screens." },
          ].map((m) => {
            const on = (group.entry_mode ?? "fields") === m.mode;
            return (
              <button key={m.mode} type="button" disabled={on}
                onClick={() => window.confirm(`Switch "${group.name}" to ${m.title}? New ERP entries use it straight away.`)
                  && act(() => onboardingApi.setEntryMode(group.id, m.mode as "excel" | "fields", (group.excel_config ?? undefined) as never), `Entry mode: ${m.title}.`)}
                className={`rounded-xl border p-3 text-left ${on ? "border-indigo-500 bg-indigo-50 ring-1 ring-indigo-200 dark:bg-indigo-500/10 dark:ring-indigo-500/30" : "border-slate-200 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"}`}>
                <span className="block text-sm font-semibold">{on ? "● " : ""}{m.title}</span>
                <span className={HELP}>{m.text}</span>
              </button>
            );
          })}
        </div>
        {isExcel && cfg && <p className={HELP}>{cfg.file_name ?? "import file"} · {(cfg.sheets ?? []).length} sheet(s) mapped. Column rules are set per field in Steps 1 and 4 (Live template panel).</p>}
        {!isExcel && !(cfg?.sheets ?? []).length && <p className={HELP}>To switch to Excel import, map at least one sheet in Step 1 first, or the switch is refused.</p>}
        <p className="text-sm">
          ERP script: {ready ? <b className="text-emerald-700 dark:text-emerald-300">{ready.name} (ready)</b> : <b className="text-amber-700 dark:text-amber-300">no ready script linked - jobs cannot go to the ERP</b>}
        </p>
      </Section>

      <Section title="ERP scripts" help="A job uses the first READY script linked to its template. The branch field routes each job to that branch's ERP login. Record or edit a script's steps in the ERP Script recorder.">
        {scripts.length === 0 && <p className={HELP}>This customer has no ERP scripts yet - create one below.</p>}
        {scripts.map((s) => {
          const on = (s.template_ids ?? []).includes(group.id);
          return (
            <div key={s.id} className="flex flex-col gap-2 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" className="h-4 w-4 accent-indigo-600" checked={on} onChange={(e) => {
                    const ids = new Set(s.template_ids ?? []);
                    if (e.target.checked) ids.add(group.id); else ids.delete(group.id);
                    void act(() => updateErpScript(s.id, { template_ids: [...ids] }).then(load), `${s.name} ${e.target.checked ? "linked to" : "unlinked from"} this template.`);
                  }} />
                  <b>{s.name}</b>
                </label>
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${s.status === "ready" ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"}`}>
                  {s.status === "ready" ? "Ready" : "Draft"}
                </span>
                <span className={HELP}>{(s.steps ?? []).length} steps</span>
                <div className="ml-auto flex gap-2">
                  <a href={`/super-admin/erp-scripts/${s.id}`} target="_blank" rel="noopener noreferrer"
                    className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800">Open recorder ↗</a>
                  <Button size="sm" variant="secondary" disabled={s.status !== "ready" && !(s.steps ?? []).length}
                    onClick={() => act(() => updateErpScript(s.id, { status: s.status === "ready" ? "draft" : "ready" }).then(load),
                      s.status === "ready" ? `${s.name} is back to draft - jobs no longer use it.` : `${s.name} is ready - jobs use it.`)}>
                    {s.status === "ready" ? "Back to draft" : "Mark ready"}
                  </Button>
                </div>
              </div>
              {on && (
                <div className="flex items-center gap-2">
                  <input className={INPUT} placeholder="Branch field (e.g. Destination) - blank = one login for all" value={branch[s.id] ?? ""} onChange={(e) => setBranch({ ...branch, [s.id]: e.target.value })} />
                  <Button size="sm" variant="secondary" disabled={(branch[s.id] ?? "") === (s.branch_field ?? "")}
                    onClick={() => act(() => updateErpScript(s.id, { branch_field: (branch[s.id] ?? "").trim() || null }).then(load), "Branch field saved.")}>Save</Button>
                </div>
              )}
            </div>
          );
        })}

        <div className="flex flex-col gap-2 rounded-xl border border-dashed border-indigo-300 bg-indigo-50/40 p-3 dark:border-indigo-500/30 dark:bg-indigo-500/5">
          <p className="text-sm font-semibold">+ Create a new ERP script for this template</p>
          <div className="grid gap-2 sm:grid-cols-2">
            <input className={INPUT} placeholder="Script name, e.g. SOFTLINK Air Import" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            <input className={INPUT} placeholder="ERP address, e.g. https://erp.example.com/login" value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" className="accent-indigo-600" checked={draft.login} onChange={(e) => setDraft({ ...draft, login: e.target.checked })} />
            The ERP needs a login
          </label>
          {draft.login && (
            <div className="grid gap-2 sm:grid-cols-2">
              <input className={INPUT} placeholder="ERP username" autoComplete="off" value={draft.username} onChange={(e) => setDraft({ ...draft, username: e.target.value })} />
              <input className={INPUT} placeholder="ERP password" type="password" autoComplete="new-password" value={draft.password} onChange={(e) => setDraft({ ...draft, password: e.target.value })} />
            </div>
          )}
          <input className={INPUT} placeholder="Branch field (optional, e.g. Destination)" value={draft.branchField} onChange={(e) => setDraft({ ...draft, branchField: e.target.value })} />
          <div className="flex items-center gap-2">
            <Button size="sm" disabled={!draft.name.trim() || !/^https?:\/\//i.test(draft.url.trim())}
              onClick={() => act(createScript, "Script created and linked to this template - the recorder opened in a new tab to record its steps. Mark it Ready when done.")}>
              Create script & open recorder
            </Button>
            <span className={HELP}>It starts as a Draft; jobs use it once you record its steps and press Mark ready.</span>
          </div>
        </div>
      </Section>
    </>
  );
}

// ---- Reference data ----------------------------------------------------------------------------------

function ReferenceDataSection({ group, act, openField }: { group: LiveGroup; act: Act; openField: OpenField }) {
  const [sheet, setSheet] = useState<ReferenceSheet | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function load() {
    try { setSheet(await onboardingApi.getReferenceSheet(group.id)); } catch { setSheet(null); }
  }
  useEffect(() => { void load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [group.id]);
  useEffect(() => {
    if (!sheet?.processing) return;
    const t = window.setTimeout(() => void load(), 3000);
    return () => window.clearTimeout(t);
  }, [sheet]); // eslint-disable-line react-hooks/exhaustive-deps

  const lookupFields = [
    ...group.documents.flatMap((d) => d.marks.filter((m) => m.is_target_value && m.width > 0).map((m) => ({ ref: `m:${m.id}`, label: m.label_name, where: d.name }))),
    ...(group.custom_fields ?? []).filter((c) => c.is_target_value || c.kind === "lookup").map((c) => ({ ref: `c:${c.id}`, label: c.label_name, where: c.kind === "lookup" ? "lookup in sheet" : "custom field" })),
  ];

  return (
    <>
      <Section title="Material master (CTH dump)" help="The customer's own export - e.g. part code + description → CTH. Lookup fields read it.">
        <p className="text-sm">
          {sheet?.attached ? <>{sheet.file_name} · <b>{sheet.materials}</b> keys{sheet.processing ? " · processing…" : ""}</> : "No material master uploaded."}
        </p>
        <div className="flex flex-wrap gap-2">
          <label className="cursor-pointer rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700">
            {sheet?.attached ? "Replace" : "Upload"}
            <input type="file" accept=".xlsx,.xls,.csv" className="hidden" onChange={async (e) => {
              const f = e.target.files?.[0]; e.target.value = "";
              if (!f) return;
              setBusy(true);
              try { setSheet(await onboardingApi.uploadReferenceSheet(group.id, f)); setNote("Uploaded - reading it…"); } catch (err) { setNote(apiError(err, "Upload failed.")); } finally { setBusy(false); }
            }} />
          </label>
          {sheet?.attached && (
            <Button size="sm" variant="danger" isLoading={busy} onClick={async () => {
              if (!window.confirm("Remove the material master? Lookup fields stop finding values from it.")) return;
              try { await onboardingApi.deleteReferenceSheet(group.id); await load(); setNote("Removed."); } catch (err) { setNote(apiError(err, "Could not remove.")); }
            }}>Remove</Button>
          )}
        </div>
        {note && <p className={HELP}>{note}</p>}
      </Section>
      <Section title="Fields with their own reference table" help="Open a field to add, upload, search or delete its rows (e.g. the 5,890 port codes, importer branches with GSTIN).">
        <NewFieldRow group={group} act={act} openField={openField}
          kinds={["reference", "lookup"]} placeholder="New lookup field, e.g. Shipment Port Code" />
        {lookupFields.length === 0 && <p className={HELP}>No field looks values up yet - create one above, or tick "Look the value up" in a field's settings.</p>}
        {lookupFields.map((f) => (
          <div key={f.ref} className="flex items-center gap-2 text-sm">
            <span className="font-medium">{f.label}</span><span className={HELP}>{f.where}</span>
            <Button size="sm" variant="secondary" className="ml-auto" onClick={() => openField(f.ref)}>Open</Button>
          </div>
        ))}
      </Section>
    </>
  );
}

// ---- Cross-document checks ----------------------------------------------------------------------------

function CrossDocSection({ group, act, openField }: { group: LiveGroup; act: Act; openField: OpenField }) {
  const marks = new Map(group.documents.flatMap((d) => d.marks.map((m) => [m.id, `${m.label_name} (${d.name})`] as const)));
  const cfs = new Map((group.custom_fields ?? []).map((c) => [c.id, c.label_name] as const));
  const [pick, setPick] = useState("");
  return (
    <Section title="Cross-document checks" help="Each pair must hold the same value; a mismatch is flagged on the job.">
      <div className="grid gap-2 rounded-xl border border-dashed border-indigo-300 bg-indigo-50/40 p-3 dark:border-indigo-500/30 dark:bg-indigo-500/5 sm:grid-cols-[1fr_auto]">
        <select className={INPUT} value={pick} onChange={(e) => setPick(e.target.value)}>
          <option value="">+ Add a check - pick the field that must match another document…</option>
          {group.documents.flatMap((d) => d.marks.filter((m) => m.width > 0).map((m) => (
            <option key={m.id} value={`m:${m.id}`}>{m.label_name} · box on {d.name}</option>
          )))}
          {(group.custom_fields ?? []).map((c) => <option key={c.id} value={`c:${c.id}`}>{c.label_name} · custom field</option>)}
        </select>
        <Button size="sm" disabled={!pick} onClick={() => { openField(pick, "crosscheck"); setPick(""); }}>Choose what it must match →</Button>
      </div>
      {group.cross_doc_links.length === 0 && <p className={HELP}>No checks yet.</p>}
      {group.cross_doc_links.map((l) => (
        <div key={l.id} className="flex items-center gap-2 text-sm">
          <span>{l.source_mark_id ? marks.get(l.source_mark_id) : cfs.get(l.source_custom_field_id ?? "")}</span>
          <span className="text-slate-400">must equal</span>
          <span>{marks.get(l.target_mark_id) ?? "?"}</span>
          <button type="button" className="ml-auto text-slate-400 hover:text-rose-600" onClick={() => window.confirm("Remove this check?") && act(
            () => (l.source_custom_field_id ? onboardingApi.unlinkCustomFieldFromMark(l.source_custom_field_id, l.id) : onboardingApi.deleteCrossDocLink(l.id)),
            "Check removed.")}>✕</button>
        </div>
      ))}
    </Section>
  );
}

// ---- Combined Description ------------------------------------------------------------------------------

function CombinedSection({ group, act, openField }: { group: LiveGroup; act: Act; openField: OpenField }) {
  const [open, setOpen] = useState(false);
  const composite = (group.custom_fields ?? []).filter((c) => c.kind === "composite");
  return (
    <Section title="Combined Description" help="Fields joined from other fields per product line. The template-wide pieces and separators are set on the field; per-consignee formats are set here (needs jobs with that consignee).">
      <NewFieldRow group={group} act={act} openField={openField}
        kinds={["composite"]} placeholder="New combined field, e.g. Combined Description" />
      {composite.length === 0 && <p className={HELP}>No combined field yet - create one above.</p>}
      {composite.map((c) => (
        <div key={c.id} className="flex items-center gap-2 text-sm">
          <span className="font-medium">{c.label_name}</span>
          <span className={HELP}>{(c.composite_source_labels ?? []).map((p) => (typeof p === "string" ? p : `"${p.fixed}"`)).join(" + ") || "no pieces yet"}</span>
          <Button size="sm" variant="secondary" className="ml-auto" onClick={() => openField(`c:${c.id}`)}>Pieces (all consignees)</Button>
        </div>
      ))}
      <div><Button size="sm" onClick={() => setOpen(true)}>Per-consignee format…</Button></div>
      <CombinedDescriptionConfigModal open={open} onClose={() => setOpen(false)} groupId={group.id} />
    </Section>
  );
}

// ---- Users ------------------------------------------------------------------------------------------------

function UsersSection({ group, act }: { group: LiveGroup; act: Act }) {
  const [users, setUsers] = useState<TemplateUser[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [nu, setNu] = useState({ name: "", email: "", password: "", onlyThis: false });
  const load = () => getTemplateUsers(group.id).then(setUsers).catch(() => setUsers([]));
  useEffect(() => { void load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [group.id]);
  return (
    <Section title="Users who work on this template" help="A user with no template assigned sees every template of the customer. Assigning this one to such a user limits them to it.">
      {!users && <p className={HELP}>Loading…</p>}
      {users?.filter((u) => u.role !== "manager").map((u) => (
        <label key={u.id} className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-700">
          <input type="checkbox" className="h-4 w-4 accent-indigo-600" checked={u.assigned} onChange={async (e) => {
            const on = e.target.checked;
            if (on && u.sees_all && !window.confirm(`${u.name} now sees EVERY template. Assigning this one limits ${u.name} to it (plus any others assigned later). Continue?`)) return;
            if (!on && u.assignment_count === 1 && !window.confirm(`This is ${u.name}'s only template - removing it makes ${u.name} see EVERY template of the customer. Continue?`)) return;
            try { await setTemplateUser(group.id, u.id, on); await load(); setNote(`${u.name}: ${on ? "assigned" : "removed"}.`); } catch (err) { setNote(apiError(err, "Could not save.")); }
          }} />
          <span className="min-w-0 flex-1"><b>{u.name}</b> <span className={HELP}>{u.email} · {u.role}</span></span>
          <span className={HELP}>{u.sees_all ? "sees all templates" : `${u.assignment_count} template(s)`}</span>
        </label>
      ))}
      {note && <p className={HELP}>{note}</p>}
      <div className="flex flex-col gap-2 rounded-xl border border-dashed border-indigo-300 bg-indigo-50/40 p-3 dark:border-indigo-500/30 dark:bg-indigo-500/5">
        <p className="text-sm font-semibold">+ New Tenant Admin login for this customer</p>
        <div className="grid gap-2 sm:grid-cols-3">
          <input className={INPUT} placeholder="Full name" value={nu.name} onChange={(e) => setNu({ ...nu, name: e.target.value })} />
          <input className={INPUT} placeholder="Email" type="email" autoComplete="off" value={nu.email} onChange={(e) => setNu({ ...nu, email: e.target.value })} />
          <input className={INPUT} placeholder="Password (min. 8 characters)" type="password" autoComplete="new-password" value={nu.password} onChange={(e) => setNu({ ...nu, password: e.target.value })} />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="accent-indigo-600" checked={nu.onlyThis} onChange={(e) => setNu({ ...nu, onlyThis: e.target.checked })} />
          Only this template (unticked = sees every template of the customer)
        </label>
        <div>
          <Button size="sm" disabled={!nu.name.trim() || !nu.email.trim() || nu.password.length < 8}
            onClick={() => act(async () => {
              await createUser({ email: nu.email.trim(), password: nu.password, full_name: nu.name.trim(), role: "tenant_admin",
                tenant_id: group.tenant_id, ...(nu.onlyThis ? { template_ids: [group.id] } : {}) });
              setNu({ name: "", email: "", password: "", onlyThis: false });
              await load();
            }, "Tenant Admin login created.")}>Create login</Button>
        </div>
        <p className={HELP}>Operator and GK2 logins are created by this customer's Tenant Admin (Users page) - then tick them above.</p>
      </div>
    </Section>
  );
}

// ---- Data Transformation & approval -----------------------------------------------------------------

function TransformSection({ group, act, openField }: { group: LiveGroup; act: Act; openField: OpenField }) {
  const targets = new Set(group.cross_doc_links.map((l) => l.target_mark_id));
  const docs = group.documents.map((d) => ({ d, marks: d.marks.filter((m) => m.width > 0 && !targets.has(m.id)) })).filter((x) => x.marks.length);
  const [prompts, setPrompts] = useState<Record<string, string>>({});
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [why, setWhy] = useState("");

  useEffect(() => {
    setPrompts(Object.fromEntries(group.documents.flatMap((d) => d.marks.map((m) => [m.id, m.tenant_format_prompt ?? ""]))));
  }, [group]);

  async function check(markId: string, example: string | null) {
    setPreviews((p) => ({ ...p, [markId]: "…" }));
    try {
      const out = await reviewsApi.formatCheck(markId, { value: example, format_prompt: prompts[markId] ?? "" });
      setPreviews((p) => ({ ...p, [markId]: out || "(blank)" }));
    } catch (e) { setPreviews((p) => ({ ...p, [markId]: apiError(e, "check failed") })); }
  }

  const changed = group.documents.flatMap((d) => d.marks).filter((m) => (prompts[m.id] ?? "") !== (m.tenant_format_prompt ?? ""));

  async function saveAll() {
    for (const m of changed) await patchMark(m.id, { tenant_format_prompt: prompts[m.id] ?? "" });
  }

  return (
    <>
      <Section title="Data Transformation" help="The format rule each value goes through after it is read. Check it on the field's example value before approving."
        actions={<div className="flex gap-2">
          <Button size="sm" variant="secondary" isLoading={busy} onClick={async () => {
            setBusy(true);
            for (const x of docs) for (const m of x.marks) if (prompts[m.id]) await check(m.id, m.example_value);
            setBusy(false);
          }}>Check all</Button>
          <Button size="sm" disabled={!changed.length} onClick={() => act(saveAll, `Saved ${changed.length} format rule(s).`)}>Save {changed.length || ""} change(s)</Button>
        </div>}>
        {docs.map(({ d, marks }) => (
          <div key={d.id} className="rounded-xl border border-slate-200 p-3 dark:border-slate-700">
            <p className="mb-2 text-sm font-semibold">{d.name}</p>
            <div className="flex flex-col gap-3">
              {marks.map((m) => (
                <div key={m.id} className="grid gap-2 md:grid-cols-[12rem_1fr_14rem]">
                  <div className="text-sm">
                    <button type="button" className="font-medium text-indigo-700 hover:underline dark:text-indigo-300" onClick={() => openField(`m:${m.id}`)}>{m.display_label || m.label_name}</button>
                    <p className={`truncate font-mono ${HELP}`}>{m.example_value || "(no example)"}</p>
                  </div>
                  <textarea className={`${INPUT} font-mono text-xs`} rows={2} placeholder="No format rule" value={prompts[m.id] ?? ""}
                    onChange={(e) => setPrompts({ ...prompts, [m.id]: e.target.value })} />
                  <div className="flex flex-col gap-1">
                    <Button size="sm" variant="secondary" disabled={!prompts[m.id]} onClick={() => check(m.id, m.example_value)}>Check</Button>
                    {previews[m.id] && <span className="break-all font-mono text-xs text-emerald-700 dark:text-emerald-300">→ {previews[m.id]}</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </Section>
      <Section title="Approval" help="Approving makes the template live (same as the green button at the top, without its checklist). Not approving sends the message to the template's inbox.">
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => act(async () => { if (changed.length) await saveAll(); await reviewsApi.approveTemplate(group.id); }, "Template approved - it is live.")}>Approve (make live)</Button>
        </div>
        <textarea className={INPUT} rows={2} placeholder="What needs changing (for Not approve)" value={why} onChange={(e) => setWhy(e.target.value)} />
        <div><Button variant="danger" size="sm" disabled={!why.trim()} onClick={() => act(() => reviewsApi.requestChanges(group.id, why.trim()), "Changes requested.")}>Not approve</Button></div>
      </Section>
    </>
  );
}

// ---- Test on samples ----------------------------------------------------------------------------------

function TestSection({ group }: { group: LiveGroup }) {
  const [results, setResults] = useState<Record<string, DemoResult | string>>({});
  async function run(docId: string) {
    setResults((r) => ({ ...r, [docId]: "Reading…" }));
    try { const out = await onboardingApi.demoExtract(docId); setResults((r) => ({ ...r, [docId]: out })); }
    catch (e) { setResults((r) => ({ ...r, [docId]: apiError(e, "Could not read the sample.") })); }
  }
  return (
    <Section title="Test on the template samples" help="Reads each document's sample with the live settings - what a job would get.">
      {group.documents.filter((d) => d.is_uploaded).map((d) => {
        const r = results[d.id];
        return (
          <div key={d.id} className="rounded-xl border border-slate-200 p-3 dark:border-slate-700">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold">{d.name}</span>
              <Button size="sm" variant="secondary" className="ml-auto" onClick={() => run(d.id)}>Read sample</Button>
            </div>
            {typeof r === "string" && <p className={`mt-2 ${HELP}`}>{r}</p>}
            {r && typeof r !== "string" && (
              <table className="mt-2 w-full text-xs">
                <tbody>
                  {r.results.map((x) => (
                    <tr key={x.mark_id} className="border-t border-slate-100 dark:border-slate-800">
                      <td className="py-1 pr-2 font-medium">{x.label_name}</td>
                      <td className="py-1 font-mono">{x.extracted_values?.length ? x.extracted_values.join(" | ") : (x.extracted_value ?? <span className="text-rose-500">nothing read</span>)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        );
      })}
    </Section>
  );
}

// ---- Classifier samples, prompt history, built-in names ------------------------------------------------

function SamplesSection({ group }: { group: LiveGroup }) {
  const [rows, setRows] = useState<ClassifierSample[] | null>(null);
  const load = () => getClassifierSamples(group.id).then(setRows).catch(() => setRows([]));
  useEffect(() => { void load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [group.id]);
  return (
    <Section title="Classifier samples" help="Page texts the classifier compares new pages with to tell which document a page is. Remove one that was learned from a wrong page.">
      {rows?.length === 0 && <p className={HELP}>No samples learned yet.</p>}
      <div className="flex max-h-[32rem] flex-col gap-2 overflow-y-auto">
        {rows?.map((s) => (
          <div key={s.id} className="rounded-lg border border-slate-200 p-2 text-xs dark:border-slate-700">
            <div className="mb-1 flex items-center gap-2">
              <b>{s.document}</b><span className={HELP}>{s.created_at ? new Date(s.created_at).toLocaleString() : ""}</span>
              <button type="button" className="ml-auto text-slate-400 hover:text-rose-600"
                onClick={async () => { if (window.confirm("Remove this sample?")) { await deleteClassifierSample(s.id); await load(); } }}>✕</button>
            </div>
            <p className="line-clamp-3 whitespace-pre-wrap font-mono text-slate-600 dark:text-slate-300">{s.excerpt}</p>
          </div>
        ))}
      </div>
    </Section>
  );
}

function HistoryTable({ group }: { group: LiveGroup }) {
  const [rows, setRows] = useState<PromptHistoryRow[] | null>(null);
  const [q, setQ] = useState("");
  useEffect(() => { getPromptHistory(group.id).then(setRows).catch(() => setRows([])); }, [group.id]);
  const shown = useMemo(() => (rows ?? []).filter((r) => !q || r.label_name.toLowerCase().includes(q.toLowerCase())), [rows, q]);
  return (
    <Section title="Prompt history" help="Every prompt / format-hint change made through Fix prompt, newest first."
      actions={<input className={`${INPUT} w-56`} placeholder="Filter by field" value={q} onChange={(e) => setQ(e.target.value)} />}>
      {rows?.length === 0 && <p className={HELP}>No prompt changes recorded.</p>}
      <div className="flex max-h-[32rem] flex-col gap-2 overflow-y-auto">
        {shown.map((r) => (
          <div key={r.id} className="rounded-lg border border-slate-200 p-2 text-xs dark:border-slate-700">
            <p className="font-semibold">{r.label_name} · {r.action} · {r.changed_by ?? "—"} · {r.created_at ? new Date(r.created_at).toLocaleString() : ""}</p>
            {r.new_prompt !== r.old_prompt && <p className="mt-1 line-clamp-4 whitespace-pre-wrap font-mono text-slate-600 dark:text-slate-300">{r.new_prompt}</p>}
          </div>
        ))}
      </div>
    </Section>
  );
}

function NamesSection({ group }: { group: LiveGroup }) {
  const [names, setNames] = useState<SpecialName[] | null>(null);
  useEffect(() => { getLinks(group.id).then((l) => setNames(l.special_names)).catch(() => setNames([])); }, [group.id]);
  return (
    <Section title="Built-in field names" help="The app has built-in behaviour for these exact names (quotation trio, CTH lookup, GSTIN rule, insurance, ports…). Spell them exactly; renaming one switches that behaviour off for this template.">
      <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
        {names?.map((n) => (
          <span key={n.name} className={`rounded-lg px-2.5 py-1.5 font-mono text-xs ${n.present ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"}`}>
            {n.present ? "✓ " : "· "}{n.name}
          </span>
        ))}
      </div>
    </Section>
  );
}
