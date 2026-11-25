import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import * as erpApi from "../../api/onboardingErp";
import * as templateApi from "../../api/onboardingTemplate";
import type { ErpCategory, ErpCustomer, ErpField, ErpWorkbook, FixedConfig, OperatorConfig, ReferenceConfig } from "../../api/onboardingErp";
import {
  OnboardingCustomer, OnboardingSteps, useOnboardingCustomer, useOnboardingFullScreen,
} from "../../components/onboarding/OnboardingSteps";
import { LiveTemplatePanel } from "../../components/onboarding/LiveTemplatePanel";
import { LiveBehaviourPanel } from "../../components/onboarding/LiveBehaviourPanel";

// Customer Onboarding - Step 4: Field Configuration. One tab per value source; click a field to
// configure it. Fixed (value, per-importer values, shown or hidden), Operator Input (question,
// hint, input type, choices, default, mandatory) and Reference (Quotation / Importer Master or
// the field's own table, picked within another field, what it fills, operator pick or auto).
// AI-Derived (rule + documents + Test), Formula (pieces + preview) and Document (label, mandatory,
// input, choices, validation, cross-document checks) are set on the live field in "How it
// behaves" (LiveBehaviourPanel) - Step 4 owns how every field behaves (recommended workflow).

const TABS: { key: ErpCategory; label: string; ready: boolean; tone: string; next: string }[] = [
  { key: "reference", label: "Reference", ready: true, tone: "text-amber-800 dark:text-amber-300", next: "" },
  { key: "fixed", label: "Fixed", ready: true, tone: "text-slate-700 dark:text-slate-200", next: "" },
  { key: "operator", label: "Operator Input", ready: true, tone: "text-pink-700 dark:text-pink-300", next: "" },
  // These three are set on the live template field itself (Live template panel below): the
  // AI instruction + documents it reads; the pieces a field joins + the ERP Excel column rules
  // (pick by, fallback, transform, fixed / per-row values); a box's extraction prompt, format
  // rule, validation and cross-document checks.
  { key: "ai", label: "Calculated (AI-Derived)", ready: false, tone: "text-violet-700 dark:text-violet-300",
    next: "The AI rule and the documents it reads, tested on the template samples; plus label, mandatory, input and validation." },
  { key: "formula", label: "Calculated (Formula / Rule)", ready: false, tone: "text-teal-700 dark:text-teal-300",
    next: "The pieces it joins (fields or fixed text) with the separator before each, previewed with Excel examples; the ERP Excel column rules are below." },
  { key: "document", label: "Document", ready: false, tone: "text-blue-700 dark:text-blue-300",
    next: "Label, mandatory, input type, choices, validation and cross-document checks. Where it sits is Step 2; how it is read (prompt, format rule) is Step 3." },
];

function errorText(err: unknown, fallback: string): string {
  return (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
}

function configured(f: ErpField): boolean {
  const c = f.config as Record<string, unknown> | null;
  if (f.category === "fixed") return !!c && (!!c.value || ((c.per_importer as unknown[]) ?? []).length > 0);
  if (f.category === "operator") return !!c && !!c.input_type;
  if (f.category === "reference") return !!c && !!c.source;
  return false;
}

export function ErpFieldConfigPage() {
  const groupId = useOnboardingCustomer("customer");
  const [customers, setCustomers] = useState<ErpCustomer[]>([]);
  const [wb, setWb] = useState<ErpWorkbook | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<ErpCategory>("reference");
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [message, setMessage] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const [fullScreen, setFullScreen] = useOnboardingFullScreen();

  useEffect(() => {
    erpApi.listCustomers().then(setCustomers).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!groupId) { setWb(null); return; }
    setLoading(true);
    erpApi.getWorkbook(groupId).then(setWb).catch(() => setMessage({ tone: "warn", text: "Could not load this customer's fields." }))
      .finally(() => setLoading(false));
  }, [groupId]);

  useEffect(() => {
    if (!fullScreen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFullScreen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullScreen, setFullScreen]);

  const customer = customers.find((c) => c.group_id === groupId) ?? null;
  const fields = useMemo(() => wb?.fields ?? [], [wb]);
  const docName = (id: string | null) => wb?.documents.find((d) => d.id === id)?.name ?? "";
  const inTab = (k: ErpCategory) => fields.filter((f) => f.category === k);
  const q = search.trim().toLowerCase();
  const list = inTab(tab).filter((f) => !q || `${f.excel_name} ${f.display_label ?? ""} ${f.sheet_name}`.toLowerCase().includes(q));
  const field = fields.find((f) => f.id === selected && f.category === tab) ?? null;
  const tabInfo = TABS.find((t) => t.key === tab)!;
  // Example values by field name (ERP Excel column / screen label), for the formula preview.
  const samples = useMemo(() => {
    const out: Record<string, string> = {};
    for (const f of fields) {
      if (!f.samples.length) continue;
      for (const k of [f.excel_name, f.display_label]) if (k && !(k in out)) out[k] = String(f.samples[0]);
    }
    return out;
  }, [fields]);

  useEffect(() => { setSelected(null); }, [tab]);

  // Saving here writes the live template (one data store). On a template that is already live
  // that changes running jobs straight away, so it is confirmed once per session.
  const [isLive, setIsLive] = useState(false);
  useEffect(() => {
    if (!groupId) return;
    templateApi.getLiveGroup(groupId).then((g) => setIsLive(g.status === "approved")).catch(() => setIsLive(false));
  }, [groupId]);
  const liveOkKey = `onboarding.liveEditOk.${groupId}`;
  const confirmLiveEdit = () => {
    if (!isLive) return true;
    try { if (sessionStorage.getItem(liveOkKey)) return true; } catch { /* ignore */ }
    const ok = window.confirm("This template is LIVE. Saving here changes the live template, and new jobs use it straight away.\n\nSave anyway?");
    if (ok) { try { sessionStorage.setItem(liveOkKey, "1"); } catch { /* ignore */ } }
    return ok;
  };

  const saveField = async (f: ErpField, patch: erpApi.ErpFieldPatch) => {
    if (!confirmLiveEdit()) return;
    try {
      const res = await erpApi.updateField(f.id, patch);
      setWb((w) => (w ? { ...w, fields: w.fields.map((x) => (x.id === f.id ? res.field : x)) } : w));
      const name = res.field.display_label || res.field.excel_name;
      setMessage(res.live
        ? { tone: "ok", text: `Saved ${name} - applied to the live template field "${res.live.label}".` }
        : { tone: "warn", text: `Saved ${name} in onboarding. It is not connected to a live field yet - use "Create" or "Link" below so jobs use it.` });
    } catch (err) {
      setMessage({ tone: "warn", text: errorText(err, "Could not save.") });
    }
  };

  return (
    <AppShell title="Field Configuration" subtitle="Customer Onboarding · Step 4 - configure how each non-document field gets its value">
      <div className={fullScreen ? "fixed inset-0 z-40 overflow-auto bg-slate-50 p-4 dark:bg-slate-950" : ""}>
        <OnboardingSteps current={4} groupId={groupId} />
        <Card className="mb-4 flex flex-wrap items-center gap-3 p-4">
          <OnboardingCustomer name={customer?.name} tenant={customer?.tenant} groupId={groupId} />
          {wb && <span className="text-xs text-slate-400">{wb.file_name} · {wb.summary.classified} classified fields</span>}
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
        {loading && <Card className="p-10 text-center text-sm text-slate-500">Loading…</Card>}

        {wb && !loading && (
          <>
            {/* Tabs */}
            <div className="mb-4 flex flex-wrap gap-1 rounded-xl border border-slate-200 bg-white p-1 dark:border-slate-700 dark:bg-slate-900">
              {TABS.map((t) => {
                const all = inTab(t.key);
                const done = all.filter(configured).length;
                const on = t.key === tab;
                return (
                  <button
                    key={t.key}
                    type="button"
                    onClick={() => setTab(t.key)}
                    className={`flex-1 rounded-lg px-3 py-2 text-left ${on ? "bg-indigo-50 ring-1 ring-indigo-200 dark:bg-indigo-500/10 dark:ring-indigo-500/30" : "hover:bg-slate-50 dark:hover:bg-slate-800"}`}
                  >
                    <span className={`block text-sm font-semibold ${t.tone}`}>{t.label}</span>
                    <span className="block text-xs text-slate-400">
                      {all.length} field{all.length === 1 ? "" : "s"}{t.ready ? ` · ${done} set` : " · on the live field"}
                    </span>
                  </button>
                );
              })}
            </div>

            <div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
              {/* Fields in this tab */}
              <Card className="h-fit p-4">
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search field…"
                  className="mb-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900" />
                {list.length === 0 && (
                  <p className="text-sm text-slate-500">
                    No {tabInfo.label} fields{q ? ` match "${search}"` : ""}. Fields get their category in <Link className="text-indigo-600 underline" to={`/super-admin/onboarding/erp-fields?customer=${groupId}`}>Step 1</Link>.
                  </p>
                )}
                <div className="flex max-h-[65vh] flex-col gap-1 overflow-y-auto">
                  {list.map((f) => {
                    const on = f.id === selected;
                    return (
                      <button
                        key={f.id}
                        type="button"
                        onClick={() => setSelected(f.id)}
                        className={`rounded-lg border px-3 py-2 text-left text-sm ${on ? "border-indigo-500 ring-2 ring-indigo-200 dark:ring-indigo-500/30" : "border-slate-200 hover:border-slate-300 dark:border-slate-700"}`}
                      >
                        <span className="flex items-center gap-2">
                          {tabInfo.ready && (configured(f)
                            ? <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-[10px] text-white">✓</span>
                            : <span className="h-3 w-3 shrink-0 rounded-full border-2 border-slate-300" />)}
                          <span className="min-w-0 flex-1 truncate font-medium text-slate-800 dark:text-slate-100">{f.display_label || f.excel_name}{f.mandatory && <span className="text-rose-500">*</span>}</span>
                          {f.sheet_name === ERP_UI && <span className="shrink-0 rounded bg-indigo-100 px-1.5 text-[10px] font-semibold text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-200">ERP UI</span>}
                        </span>
                        <span className="mt-0.5 block truncate text-[11px] text-slate-400">
                          {f.sheet_name}{docName(f.source_document) ? ` · ${docName(f.source_document)}` : ""}
                          {f.samples.length ? ` · e.g. ${f.samples.slice(0, 2).join(" · ")}` : ""}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </Card>

              {/* Configuration */}
              <Card className="h-fit p-5">
                {!field && <p className="text-sm text-slate-500">Pick a field on the left to configure it.</p>}
                {field && !tabInfo.ready && (
                  <div className="flex flex-col gap-3 text-sm">
                    <Header field={field} />
                    <p className="text-xs text-slate-500">{tabInfo.next}</p>
                    {(field.formula || field.ai_prompt || field.erp_master_ref) && (
                      <p className="text-xs text-slate-500">Note from Step 1: {field.formula || field.ai_prompt || field.erp_master_ref}</p>
                    )}
                    {/* How it behaves, on the live field it fills - then the link / column rules. */}
                    <LiveBehaviourPanel key={`beh-${field.id}`} groupId={groupId} erpFieldId={field.id} category={field.category}
                      mode="full" samples={samples} beforeSave={confirmLiveEdit} onMessage={(tone, text) => setMessage({ tone, text })} />
                    <LiveTemplatePanel key={field.id} groupId={groupId} erpFieldId={field.id} compact />
                  </div>
                )}
                {field && field.category === "fixed" && <FixedPanel key={field.id} field={field} onSave={(p) => saveField(field, p)} />}
                {field && field.category === "operator" && <OperatorPanel key={field.id} field={field} onSave={(p) => saveField(field, p)} />}
                {field && field.category === "reference" && (
                  <ReferencePanel key={field.id} field={field} fields={fields} onSave={(p) => saveField(field, p)} />
                )}
                {field && tabInfo.ready && (
                  <div className="mt-4 flex flex-col gap-4">
                    <LiveBehaviourPanel key={`beh-${field.id}`} groupId={groupId} erpFieldId={field.id} category={field.category}
                      mode="extras" samples={samples} beforeSave={confirmLiveEdit} onMessage={(tone, text) => setMessage({ tone, text })} />
                    <LiveTemplatePanel key={`live-${field.id}`} groupId={groupId} erpFieldId={field.id} compact />
                  </div>
                )}
              </Card>
            </div>

            <div className="mt-4 flex justify-end gap-2">
              <Link to={`/super-admin/onboarding/prompts?customer=${groupId}`} className="rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800">← Step 3 Prompts</Link>
              <Link to={`/super-admin/onboarding/screen-designer?group=${groupId}`} className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700">Next: Screen Designer →</Link>
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}

// The pseudo-sheet of fields the ERP UI script picks on screen (app/core/erp_ui_fields.ERP_UI_SHEET).
const ERP_UI = "ERP UI (not in Excel)";

const input = "w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100";

function Header({ field }: { field: ErpField }) {
  return (
    <div className="mb-4">
      <p className="text-base font-semibold text-slate-900 dark:text-slate-50">{field.display_label || field.excel_name}</p>
      <p className="text-xs text-slate-400">
        {field.sheet_name === ERP_UI
          ? <>Picked on the ERP screen by the ERP UI script - not in the Excel · template field {field.excel_name}</>
          : <>{field.sheet_name}{field.column_key ? ` · column ${field.column_key}` : " · custom field"} · Excel name {field.excel_name}</>}
      </p>
    </div>
  );
}

function FixedPanel({ field, onSave }: { field: ErpField; onSave: (p: erpApi.ErpFieldPatch) => Promise<void> }) {
  const start = (field.config as FixedConfig | null) ?? { value: field.default_value ?? "", per_importer: [], hidden: false };
  const [value, setValue] = useState(start.value ?? "");
  const [rows, setRows] = useState(start.per_importer ?? []);
  const [hidden, setHidden] = useState(!!start.hidden);
  const [saving, setSaving] = useState(false);
  const suggestions = [...new Set(field.samples)].filter((s) => s && s !== value).slice(0, 5);

  const save = async () => {
    setSaving(true);
    await onSave({ config: { value, per_importer: rows.filter((r) => r.importer.trim()), hidden } });
    setSaving(false);
  };

  return (
    <div className="flex flex-col gap-4">
      <Header field={field} />
      <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
        Fixed value - the same on every job
        <input className={input} value={value} onChange={(e) => setValue(e.target.value)} placeholder="e.g. N" />
      </label>
      {suggestions.length > 0 && (
        <div className="-mt-2 flex flex-wrap items-center gap-1 text-xs text-slate-500">
          From the Excel:
          {suggestions.map((s) => (
            <button key={s} type="button" onClick={() => setValue(s)} className="rounded bg-slate-100 px-2 py-0.5 font-mono text-slate-700 hover:bg-indigo-100 dark:bg-slate-800 dark:text-slate-200">{s}</button>
          ))}
        </div>
      )}

      <div>
        <p className="text-xs font-medium text-slate-500">Different value for some importers</p>
        <p className="mb-2 text-[11px] text-slate-400">For a value that only fits one importer (an end-use code, a valuation rule) - the others keep the fixed value above.</p>
        <div className="flex flex-col gap-2">
          {rows.map((r, i) => (
            <div key={i} className="flex gap-2">
              <input className={input} value={r.importer} placeholder="Importer name" onChange={(e) => setRows((rs) => rs.map((x, j) => (j === i ? { ...x, importer: e.target.value } : x)))} />
              <input className={`${input} max-w-[12rem]`} value={r.value} placeholder="Value" onChange={(e) => setRows((rs) => rs.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
              <button type="button" onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))} className="px-2 text-slate-400 hover:text-rose-500" title="Remove">✕</button>
            </div>
          ))}
          <button type="button" onClick={() => setRows((rs) => [...rs, { importer: "", value: "" }])} className="self-start text-xs text-indigo-600 hover:underline">+ Add importer</button>
        </div>
      </div>

      <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
        <input type="checkbox" checked={!hidden} onChange={(e) => setHidden(!e.target.checked)} />
        Show it on the operator screen (untick to send it to the ERP without showing it)
      </label>

      <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800/50">
        Every job sends <b className="font-mono text-slate-800 dark:text-slate-100">{value || "(blank)"}</b>
        {rows.filter((r) => r.importer.trim()).length > 0 && <> - except {rows.filter((r) => r.importer.trim()).map((r) => `${r.importer}: ${r.value || "(blank)"}`).join(", ")}</>}.
      </div>
      <div><Button isLoading={saving} onClick={() => void save()}>Save</Button></div>
    </div>
  );
}

function OperatorPanel({ field, onSave }: { field: ErpField; onSave: (p: erpApi.ErpFieldPatch) => Promise<void> }) {
  const start = (field.config as OperatorConfig | null) ?? {
    input_type: field.data_type === "date" ? "date" : field.data_type === "number" || field.data_type === "decimal" ? "number" : "text",
    choices: [], default: field.default_value ?? "",
  };
  const [label, setLabel] = useState(field.display_label ?? field.excel_name);
  const [hint, setHint] = useState(field.description ?? "");
  const [type, setType] = useState<OperatorConfig["input_type"]>(start.input_type);
  const [choices, setChoices] = useState((start.choices ?? []).join("\n") || (start.input_type === "dropdown" ? "" : [...new Set(field.samples)].join("\n")));
  const [def, setDef] = useState(start.default ?? "");
  const [mandatory, setMandatory] = useState(field.mandatory);
  const [saving, setSaving] = useState(false);
  const choiceList = choices.split("\n").map((c) => c.trim()).filter(Boolean);

  const save = async () => {
    setSaving(true);
    await onSave({
      display_label: label.trim() || null,
      description: hint.trim() || null,
      mandatory,
      config: { input_type: type, choices: type === "dropdown" ? choiceList : [], default: def },
    });
    setSaving(false);
  };

  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <div className="flex flex-col gap-3">
        <Header field={field} />
        <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
          Question (label on the operator screen)
          <input className={input} value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
          Hint under the box
          <input className={input} value={hint} onChange={(e) => setHint(e.target.value)} placeholder="e.g. Enter T for Transaction, D for Deferred" />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
          Input type
          <select className={input} value={type} onChange={(e) => setType(e.target.value as OperatorConfig["input_type"])}>
            <option value="text">Text</option>
            <option value="number">Number</option>
            <option value="date">Date</option>
            <option value="dropdown">Dropdown - pick from a list</option>
          </select>
        </label>
        {type === "dropdown" && (
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
            Choices (one per line)
            <textarea className={`${input} font-mono`} rows={4} value={choices} onChange={(e) => setChoices(e.target.value)} placeholder={"T - Transaction\nD - Deferred"} />
            {field.samples.length > 0 && <span className="font-normal text-slate-400">Values in the Excel: {field.samples.join(" · ")}</span>}
          </label>
        )}
        <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
          Default value (pre-filled; the operator can change it)
          {type === "dropdown"
            ? (
              <select className={input} value={def} onChange={(e) => setDef(e.target.value)}>
                <option value="">No default</option>
                {choiceList.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            )
            : <input className={input} type={type === "date" ? "date" : type === "number" ? "number" : "text"} value={def} onChange={(e) => setDef(e.target.value)} />}
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
          <input type="checkbox" checked={mandatory} onChange={(e) => setMandatory(e.target.checked)} />
          Mandatory - the job cannot be submitted until it is answered
        </label>
        <div><Button isLoading={saving} onClick={() => void save()}>Save</Button></div>
      </div>

      {/* Preview - as on the operator's Additional Details page */}
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Preview · Additional Details</p>
        <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
          <p className="mb-1.5 text-sm font-medium text-slate-800 dark:text-slate-100">{label || field.excel_name}{mandatory && <span className="text-rose-500">*</span>}</p>
          {type === "dropdown" ? (
            <select className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm dark:border-slate-700 dark:bg-slate-900" value={def} onChange={() => undefined}>
              <option value="">Pick…</option>
              {choiceList.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          ) : (
            <input readOnly className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm dark:border-slate-700 dark:bg-slate-900"
              type={type === "date" ? "date" : type === "number" ? "number" : "text"} value={def} placeholder={def ? undefined : "not found"} />
          )}
          {hint && <p className="mt-1.5 text-xs text-slate-500">{hint}</p>}
        </div>
      </div>
    </div>
  );
}

const SOURCES: { key: ReferenceConfig["source"]; label: string; help: string }[] = [
  { key: "quotation_master", label: "Quotation Master", help: "Tenant Admin → ERP Master Data - Quotation: ERP quotation name → branch → address." },
  { key: "importer_master", label: "Importer Master", help: "Tenant Admin → ERP Master Data - Importer: ERP importer name → branch → address (with GSTIN)." },
  { key: "own_table", label: "The field's own reference table", help: "A table kept for this field alone, looked up by another field's value." },
];
const LEVELS: { key: ReferenceConfig["level"]; label: string }[] = [
  { key: "name", label: "Name" }, { key: "branch", label: "Branch" }, { key: "address", label: "Address" },
];

function ReferencePanel({ field, fields, onSave }: {
  field: ErpField;
  fields: ErpField[];
  onSave: (p: erpApi.ErpFieldPatch) => Promise<void>;
}) {
  const start = (field.config as ReferenceConfig | null) ?? {
    source: "own_table", level: "", depends_on: "", fills: "", key_field: "", pick: "operator",
  };
  const [label, setLabel] = useState(field.display_label ?? field.excel_name);
  const [source, setSource] = useState<ReferenceConfig["source"]>(start.source);
  const [level, setLevel] = useState<ReferenceConfig["level"]>(start.level || "name");
  const [dependsOn, setDependsOn] = useState(start.depends_on ?? "");
  const [fills, setFills] = useState(start.fills ?? "");
  const [keyField, setKeyField] = useState(start.key_field ?? "");
  const [pick, setPick] = useState<ReferenceConfig["pick"]>(start.pick ?? "operator");
  const [mandatory, setMandatory] = useState(field.mandatory);
  const [saving, setSaving] = useState(false);
  const master = source !== "own_table";
  const names = [...new Set(fields.filter((f) => f.id !== field.id).flatMap((f) => [f.excel_name, f.display_label ?? ""]).filter(Boolean))].sort();
  const shown = (name: string) => {
    const f = fields.find((x) => x.excel_name === name);
    return f?.display_label && f.display_label !== name ? `${f.display_label} (${name})` : name;
  };

  const save = async () => {
    setSaving(true);
    await onSave({
      display_label: label.trim() || null,
      mandatory,
      config: {
        source, level: master ? level : "", depends_on: dependsOn.trim(), fills: fills.trim(),
        key_field: master ? "" : keyField.trim(), pick,
      },
    });
    setSaving(false);
  };

  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <div className="flex flex-col gap-3">
        <Header field={field} />
        <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
          Label on the operator screen
          <input className={input} value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
        <div className="flex flex-col gap-1 text-xs font-medium text-slate-500">
          Where the value is picked from
          <div className="flex flex-col gap-1">
            {SOURCES.map((x) => (
              <label key={x.key} className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 ${source === x.key ? "border-indigo-400 bg-indigo-50/50 dark:bg-indigo-500/10" : "border-slate-200 dark:border-slate-700"}`}>
                <input type="radio" className="mt-0.5" checked={source === x.key} onChange={() => setSource(x.key)} />
                <span>
                  <span className="block text-sm font-medium text-slate-800 dark:text-slate-100">{x.label}</span>
                  <span className="block font-normal text-slate-400">{x.help}</span>
                </span>
              </label>
            ))}
          </div>
        </div>
        {master && (
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
            Which part of the master
            <select className={input} value={level} onChange={(e) => setLevel(e.target.value as ReferenceConfig["level"])}>
              {LEVELS.map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
            </select>
          </label>
        )}
        {!master && (
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
            Looked up by (the field whose value finds the row)
            <input className={input} list="ref-field-names" value={keyField} onChange={(e) => setKeyField(e.target.value)} placeholder="e.g. Quotation Name" />
          </label>
        )}
        <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
          Picked within (only the rows of this field's chosen value are offered)
          <input className={input} list="ref-field-names" value={dependsOn} onChange={(e) => setDependsOn(e.target.value)} placeholder="e.g. Erp Entry Name - leave blank when it stands alone" />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
          Fills, once picked
          <input className={input} list="ref-field-names" value={fills} onChange={(e) => setFills(e.target.value)} placeholder="e.g. quotation_address - leave blank for none" />
        </label>
        <datalist id="ref-field-names">{names.map((n) => <option key={n} value={n} />)}</datalist>
        <div className="flex flex-col gap-1 text-xs font-medium text-slate-500">
          How it is chosen
          <label className="flex items-center gap-2 text-sm font-normal text-slate-700 dark:text-slate-200">
            <input type="radio" checked={pick === "operator"} onChange={() => setPick("operator")} /> The operator picks from the list
          </label>
          <label className="flex items-center gap-2 text-sm font-normal text-slate-700 dark:text-slate-200">
            <input type="radio" checked={pick === "auto"} onChange={() => setPick("auto")} /> Filled by itself when exactly one row matches (the operator can still change it)
          </label>
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
          <input type="checkbox" checked={mandatory} onChange={(e) => setMandatory(e.target.checked)} />
          Mandatory - the job cannot go to the ERP without it
        </label>
        {field.erp_master_ref && <p className="text-xs text-slate-400">How the template does it today: {field.erp_master_ref}</p>}
        <div><Button isLoading={saving} onClick={() => void save()}>Save</Button></div>
      </div>

      {/* Preview - as on the operator's Additional Details page */}
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Preview · Additional Details</p>
        <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
          <p className="mb-1.5 text-sm font-medium text-slate-800 dark:text-slate-100">{label || field.excel_name}{mandatory && <span className="text-rose-500">*</span>}</p>
          <select className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm dark:border-slate-700 dark:bg-slate-900" value="" onChange={() => undefined}>
            <option value="">{pick === "auto" ? "Filled when one row matches…" : "Pick…"}</option>
          </select>
          <p className="mt-1.5 text-xs text-slate-500">
            From the {SOURCES.find((x) => x.key === source)?.label}{master ? ` (${level})` : ""}
            {!master && keyField ? `, found by ${shown(keyField)}` : ""}
            {dependsOn ? `, listing only the rows of the chosen ${shown(dependsOn)}` : ""}.
            {fills ? ` Picking it fills ${shown(fills)}.` : ""}
          </p>
          {field.sheet_name === ERP_UI && <p className="mt-1 text-[11px] text-indigo-600 dark:text-indigo-300">Selected on the ERP screen by the ERP UI script - not written into the Excel.</p>}
        </div>
      </div>
    </div>
  );
}
