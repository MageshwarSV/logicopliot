import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { designPreview, fetchSamplePageUrl } from "../../api/onboardingErp";
import type { DesignPreview } from "../../api/onboardingErp";
import type { DragEvent, ReactNode } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { OnboardingCustomer, OnboardingSteps, useOnboardingCustomer, useOnboardingFullScreen } from "../../components/onboarding/OnboardingSteps";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import * as designerApi from "../../api/screenDesigner";
import type { DesignerField, DesignerTemplate, RecentJob } from "../../api/screenDesigner";
import * as jobsApi from "../../api/jobs";
import type { JobDetail, JobFieldValue, ScreenLayout } from "../../types/jobs";
import { formatDateTime } from "../../utils/dateTime";

// Super Admin -> Screen Designer. Lays out one template document's job screen: sections and
// their order, which field sits where, and the product card with its groups. Saves straight
// away (every save is kept in history). Field properties (label, width, multi-line,
// mandatory, dropdown list) are saved on the field itself - the same settings the template
// wizard edits. A document with no layout keeps the job screen's default behaviour.

type Selection =
  | { kind: "field"; ref: string }
  | { kind: "section"; id: string }
  | { kind: "group"; id: string }
  | null;

type DragItem =
  | { type: "field"; ref: string }
  | { type: "section"; id: string }
  | { type: "group"; id: string };

type FieldTarget =
  | { container: "section"; id: string; beforeRef?: string }
  | { container: "card"; beforeRef?: string }
  | { container: "group"; id: string; beforeRef?: string };

const DRAG_MIME = "application/x-screen-designer";

function newId(prefix: string): string {
  return `${prefix}${Math.random().toString(36).slice(2, 10)}`;
}

function fieldName(f: DesignerField | undefined, ref: string): string {
  if (!f) return "(deleted field)";
  return (f.display_label ?? "").trim() || f.label_name || ref;
}

function withoutRef(layout: ScreenLayout, ref: string): ScreenLayout {
  return {
    sections: layout.sections.map((s) => ({ ...s, fields: s.fields.filter((r) => r !== ref) })),
    product_card: {
      fields: layout.product_card.fields.filter((r) => r !== ref),
      groups: layout.product_card.groups.map((g) => ({ ...g, fields: g.fields.filter((r) => r !== ref) })),
    },
  };
}

function insertAt(list: string[], ref: string, beforeRef?: string): string[] {
  const out = list.filter((r) => r !== ref);
  const at = beforeRef ? out.indexOf(beforeRef) : -1;
  if (at < 0) out.push(ref);
  else out.splice(at, 0, ref);
  return out;
}

function placeField(layout: ScreenLayout, ref: string, target: FieldTarget): ScreenLayout {
  const next = withoutRef(layout, ref);
  if (target.container === "section") {
    next.sections = next.sections.map((s) =>
      s.id === target.id ? { ...s, fields: insertAt(s.fields, ref, target.beforeRef) } : s);
  } else if (target.container === "card") {
    next.product_card = { ...next.product_card, fields: insertAt(next.product_card.fields, ref, target.beforeRef) };
  } else {
    next.product_card = {
      ...next.product_card,
      groups: next.product_card.groups.map((g) =>
        g.id === target.id ? { ...g, fields: insertAt(g.fields, ref, target.beforeRef) } : g),
    };
  }
  return next;
}

function moveInList<T extends { id: string }>(list: T[], id: string, beforeId: string | null): T[] {
  const item = list.find((x) => x.id === id);
  if (!item) return list;
  const out = list.filter((x) => x.id !== id);
  const at = beforeId ? out.findIndex((x) => x.id === beforeId) : -1;
  if (at < 0) out.push(item);
  else out.splice(at, 0, item);
  return out;
}

function uniqueTitle(base: string, taken: string[]): string {
  const lower = new Set(taken.map((t) => t.toLowerCase()));
  if (!lower.has(base.toLowerCase())) return base;
  for (let i = 2; ; i += 1) if (!lower.has(`${base} ${i}`.toLowerCase())) return `${base} ${i}`;
}

function readDrag(e: DragEvent): DragItem | null {
  try {
    return JSON.parse(e.dataTransfer.getData(DRAG_MIME)) as DragItem;
  } catch {
    return null;
  }
}

export function ScreenDesignerPage() {
  const [params, setParams] = useSearchParams();
  // Opened as Step 5 of Customer Onboarding: show the onboarding step bar.
  const inOnboarding = useLocation().pathname.includes("/onboarding/");
  const [templates, setTemplates] = useState<DesignerTemplate[]>([]);
  // As Step 5 of Customer Onboarding the template is the one chosen in Step 0.
  const onboardingGroup = useOnboardingCustomer("group");
  const groupId = params.get("group") ?? (inOnboarding ? onboardingGroup : "");
  const docId = params.get("doc") ?? "";
  const [fields, setFields] = useState<DesignerField[]>([]);
  const [layout, setLayout] = useState<ScreenLayout | null>(null);
  const [meta, setMeta] = useState<{ updated_at: string | null; updated_by: string | null }>({ updated_at: null, updated_by: null });
  const [loading, setLoading] = useState(false);
  const [jobs, setJobs] = useState<RecentJob[]>([]);
  const [previewJobId, setPreviewJobId] = useState("");
  const [previewJob, setPreviewJob] = useState<JobDetail | null>(null);
  // No job yet (a screen is designed before the first job): onboarding's sample and values.
  const [design, setDesign] = useState<DesignPreview | null>(null);
  const [selected, setSelected] = useState<Selection>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<string | null>(null);
  const [showDoc, setShowDoc] = useState(true);
  const [docPage, setDocPage] = useState(1);
  const [fullScreen, setFullScreen] = useOnboardingFullScreen();
  const [editingLabel, setEditingLabel] = useState<string | null>(null);
  const saveSeq = useRef(0);

  useEffect(() => {
    if (!fullScreen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFullScreen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullScreen]);

  const template = templates.find((t) => t.id === groupId) ?? null;
  const fieldByRef = useMemo(() => new Map(fields.map((f) => [f.ref, f])), [fields]);

  useEffect(() => {
    designerApi.listTemplates().then(setTemplates).catch(() => setMessage("Could not load the templates."));
  }, []);

  const setSelection = (group: string, doc: string) => {
    const next = new URLSearchParams();
    if (group) next.set("group", group);
    if (doc) next.set("doc", doc);
    setParams(next, { replace: true });
    setSelected(null);
  };

  // Default to the template's first document.
  useEffect(() => {
    if (template && !docId && template.documents.length) setSelection(template.id, template.documents[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [template, docId]);

  const loadDocument = useCallback(async () => {
    if (!docId) return;
    setLoading(true);
    setMessage(null);
    try {
      const [f, l] = await Promise.all([designerApi.listFields(docId), designerApi.getLayout(docId)]);
      setFields(f.fields);
      setLayout(l.layout);
      setMeta({ updated_at: l.updated_at, updated_by: l.updated_by });
    } catch {
      setMessage("Could not load this document.");
    } finally {
      setLoading(false);
    }
  }, [docId]);

  useEffect(() => {
    void loadDocument();
  }, [loadDocument]);

  useEffect(() => {
    if (!groupId) return;
    designerApi.recentJobs(groupId).then((list) => {
      setJobs(list);
      setPreviewJobId((current) => (current && list.some((j) => j.id === current) ? current : list[0]?.id ?? ""));
    }).catch(() => setJobs([]));
  }, [groupId]);

  useEffect(() => {
    if (!previewJobId) {
      setPreviewJob(null);
      return;
    }
    jobsApi.getJob(previewJobId).then(setPreviewJob).catch(() => setPreviewJob(null));
  }, [previewJobId]);

  useEffect(() => {
    if (previewJobId || !groupId || !docId) { setDesign(null); return; }
    designPreview(groupId, docId).then(setDesign).catch(() => setDesign(null));
  }, [previewJobId, groupId, docId]);

  // ---- values from the preview job --------------------------------------------------------
  const values = useMemo(() => {
    const out = new Map<string, string>();
    if (!previewJob) {
      for (const [ref, v] of Object.entries(design?.values ?? {})) out.set(ref, v.value);
      return out;
    }
    const docJobIds = new Set(previewJob.documents.filter((d) => d.template_document_id === docId).map((d) => d.id));
    const value = (fv: JobFieldValue) => (fv.value ?? fv.corrected_value ?? fv.extracted_value ?? "").toString();
    const rows = previewJob.field_values.filter((fv) => fv.row_index != null && fv.job_document_id && docJobIds.has(fv.job_document_id));
    const firstRow = rows.length ? Math.min(...rows.map((fv) => fv.row_index as number)) : null;
    for (const fv of previewJob.field_values) {
      if (fv.mark_id && fv.job_document_id && docJobIds.has(fv.job_document_id)) {
        const ref = `m:${fv.mark_id}`;
        if (out.has(ref)) continue;
        if (fv.row_index == null || fv.row_index === firstRow) out.set(ref, value(fv));
      } else if (!fv.mark_id && fv.custom_field_id && fv.row_index == null) {
        const ref = `c:${fv.custom_field_id}`;
        if (!out.has(ref)) out.set(ref, value(fv));
      }
    }
    return out;
  }, [previewJob, docId, design]);
  const designSample = !previewJob && design?.sample && design.workbook_id && design.doc_id
    ? { workbookId: design.workbook_id, docId: design.doc_id, ...design.sample } : null;

  const previewJobDoc = previewJob?.documents.find((d) => d.template_document_id === docId && (d.page_count ?? 0) > 0) ?? null;
  useEffect(() => { setDocPage(1); }, [docId, previewJobId]);

  // ---- saving -----------------------------------------------------------------------------
  const persist = async (next: ScreenLayout) => {
    setLayout(next);
    const seq = ++saveSeq.current;
    setSaveState("saving");
    try {
      const res = await designerApi.saveLayout(docId, next);
      if (seq !== saveSeq.current) return;
      setMeta({ updated_at: res.updated_at, updated_by: res.updated_by });
      setSaveState("saved");
      if (res.dropped?.length) setMessage(`${res.dropped.length} deleted field(s) were taken out of the layout.`);
    } catch (err: unknown) {
      if (seq !== saveSeq.current) return;
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setSaveState("error");
      setMessage(detail ?? "Could not save - the last saved layout has been reloaded.");
      void loadDocument();
    }
  };

  const createFromCurrent = async () => {
    setSaveState("saving");
    try {
      const res = await designerApi.createFromCurrent(docId);
      setLayout(res.layout);
      setMeta({ updated_at: res.updated_at, updated_by: res.updated_by });
      setSaveState("saved");
      setTemplates((ts) => ts.map((t) => ({ ...t, documents: t.documents.map((d) => (d.id === docId ? { ...d, has_layout: true } : d)) })));
    } catch {
      setSaveState("error");
      setMessage("Could not create the layout.");
    }
  };

  const removeLayout = async () => {
    if (!window.confirm("Remove this layout? The document goes back to the default job screen. It stays in the history.")) return;
    await designerApi.removeLayout(docId);
    setLayout(null);
    setSelected(null);
    setMeta({ updated_at: null, updated_by: null });
    setTemplates((ts) => ts.map((t) => ({ ...t, documents: t.documents.map((d) => (d.id === docId ? { ...d, has_layout: false } : d)) })));
  };

  // ---- layout edits -----------------------------------------------------------------------
  const placed = useMemo(() => {
    const s = new Set<string>();
    if (!layout) return s;
    layout.sections.forEach((x) => x.fields.forEach((r) => s.add(r)));
    layout.product_card.fields.forEach((r) => s.add(r));
    layout.product_card.groups.forEach((g) => g.fields.forEach((r) => s.add(r)));
    return s;
  }, [layout]);
  const unplaced = fields.filter((f) => !placed.has(f.ref) && (f.place === "card" || f.source === "mark" || f.on_this_tab));
  const jobLevelOffTab = fields.filter((f) => f.source === "custom" && !f.on_this_tab && !placed.has(f.ref));

  const dropField = (ref: string, target: FieldTarget) => {
    if (!layout) return;
    const field = fieldByRef.get(ref);
    const wantsCard = target.container !== "section";
    if (field && (field.place === "card") !== wantsCard) {
      setMessage(field.place === "card"
        ? "A product-line field can only go on the product card."
        : "A document field can only go in a section, not on the product card.");
      return;
    }
    setMessage(null);
    void persist(placeField(layout, ref, target));
  };

  const addSection = () => {
    if (!layout) return;
    const id = newId("s");
    const title = uniqueTitle("New section", layout.sections.map((s) => s.title));
    void persist({ ...layout, sections: [...layout.sections, { id, title, fields: [] }] });
    setSelected({ kind: "section", id });
  };

  const addGroup = () => {
    if (!layout) return;
    const id = newId("g");
    const title = uniqueTitle("New group", layout.product_card.groups.map((g) => g.title));
    void persist({ ...layout, product_card: { ...layout.product_card, groups: [...layout.product_card.groups, { id, title, fields: [] }] } });
    setSelected({ kind: "group", id });
  };

  const onDragStart = (item: DragItem) => (e: DragEvent) => {
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify(item));
    e.dataTransfer.effectAllowed = "move";
    e.stopPropagation();
  };

  const allowDrop = (key: string) => (e: DragEvent) => {
    if (!e.dataTransfer.types.includes(DRAG_MIME)) return;
    e.preventDefault();
    e.stopPropagation();
    if (dragOver !== key) setDragOver(key);
  };

  const onDropField = (target: FieldTarget) => (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(null);
    const item = readDrag(e);
    if (item?.type === "field") dropField(item.ref, target);
  };

  const onDropSection = (beforeId: string | null) => (e: DragEvent) => {
    const item = readDrag(e);
    if (item?.type !== "section" || !layout) return;
    e.preventDefault();
    e.stopPropagation();
    setDragOver(null);
    void persist({ ...layout, sections: moveInList(layout.sections, item.id, beforeId) });
  };

  const onDropGroup = (beforeId: string | null) => (e: DragEvent) => {
    const item = readDrag(e);
    if (item?.type !== "group" || !layout) return;
    e.preventDefault();
    e.stopPropagation();
    setDragOver(null);
    void persist({ ...layout, product_card: { ...layout.product_card, groups: moveInList(layout.product_card.groups, item.id, beforeId) } });
  };

  // ---- rendering --------------------------------------------------------------------------
  // The operator screen's colour language (see JobRunPage's ColourKey): blue read off a
  // document, green from a reference sheet, violet worked out, red blank.
  const toneFor = (f: DesignerField | undefined, value: string) => {
    if (!value) return "border-rose-300 bg-rose-50/60 dark:border-rose-500/40 dark:bg-rose-500/10";
    if (!f || f.source === "mark") return "border-sky-200 bg-sky-50 dark:border-sky-500/30 dark:bg-sky-500/10";
    if (f.kind === "lookup") return "border-emerald-200 bg-emerald-50 dark:border-emerald-500/30 dark:bg-emerald-500/10";
    if (f.kind === "ai" || f.kind === "composite") return "border-violet-200 bg-violet-50 dark:border-violet-500/30 dark:bg-violet-500/10";
    return "border-slate-200 bg-slate-50 dark:border-slate-600 dark:bg-slate-800/50";
  };

  const saveLabel = async (f: DesignerField, raw: string) => {
    setEditingLabel(null);
    const v = raw.trim();
    if (v === (f.display_label ?? "").trim() || (!v && !f.display_label)) return;
    try {
      await designerApi.patchField(f, { display_label: v || null });
      setFields((fs) => fs.map((x) => (x.ref === f.ref ? { ...x, display_label: v || null } : x)));
    } catch {
      setMessage(`Could not save the label of ${f.label_name}.`);
    }
  };

  const fieldBox = (ref: string, target: FieldTarget) => {
    const f = fieldByRef.get(ref);
    const isSel = selected?.kind === "field" && selected.ref === ref;
    const key = `f:${ref}`;
    const value = values.get(ref) ?? "";
    return (
      <div
        key={ref}
        draggable
        onDragStart={onDragStart({ type: "field", ref })}
        onDragOver={allowDrop(key)}
        onDragLeave={() => setDragOver((k) => (k === key ? null : k))}
        onDrop={onDropField({ ...target, beforeRef: ref } as FieldTarget)}
        onClick={(e) => {
          e.stopPropagation();
          setSelected({ kind: "field", ref });
        }}
        className={`cursor-grab rounded-lg border p-2 transition-colors ${f?.full_width ? "sm:col-span-12" : f?.quarter_width ? "sm:col-span-3" : f?.third_width ? "sm:col-span-4" : "sm:col-span-6"} ${
          isSel ? "border-indigo-500 ring-2 ring-indigo-200 dark:ring-indigo-500/30" : "border-transparent hover:border-slate-300 dark:hover:border-slate-600"
        } ${dragOver === key ? "border-t-4 border-t-indigo-500" : ""}`}
        title="Drag to move - click to edit - double-click the label to rename it"
      >
        {editingLabel === ref && f ? (
          <input
            autoFocus
            defaultValue={f.display_label ?? f.label_name}
            onClick={(e) => e.stopPropagation()}
            onBlur={(e) => void saveLabel(f, e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              if (e.key === "Escape") setEditingLabel(null);
            }}
            className="mb-1.5 w-full rounded border border-indigo-400 px-1.5 py-0.5 text-sm font-medium text-slate-800 dark:bg-slate-900 dark:text-slate-100"
          />
        ) : (
          <p
            className="mb-1.5 break-words text-sm font-medium text-slate-800 dark:text-slate-100"
            onDoubleClick={(e) => { e.stopPropagation(); setEditingLabel(ref); }}
          >
            {fieldName(f, ref)}
            {f?.mandatory && <span className="text-rose-500">*</span>}
            {f?.fixed_options?.length ? <span className="ml-1 text-[10px] text-slate-400">▾ list</span> : null}
          </p>
        )}
        <div
          className={`rounded-lg border px-3 text-sm text-slate-800 dark:text-slate-100 ${toneFor(f, value)} ${
            f?.multiline ? "min-h-[4.5rem] whitespace-pre-wrap py-2" : "truncate py-2.5"
          }`}
        >
          {value || <span className="italic text-rose-400">{previewJob ? "not found" : design ? "no value yet" : "no preview job"}</span>}
        </div>
      </div>
    );
  };

  const fieldGrid = (refs: string[], target: FieldTarget, emptyText: string) => {
    const endKey = `end:${target.container}:${"id" in target ? target.id : "card"}`;
    return (
      <div className="grid gap-x-3 gap-y-1 sm:grid-cols-12">
        {refs.map((ref) => fieldBox(ref, target))}
        <div
          onDragOver={allowDrop(endKey)}
          onDragLeave={() => setDragOver((k) => (k === endKey ? null : k))}
          onDrop={onDropField(target)}
          className={`rounded-md border border-dashed text-center sm:col-span-12 ${
            dragOver === endKey
              ? "border-indigo-400 bg-indigo-50 py-2 text-xs text-indigo-600 dark:bg-indigo-500/10"
              : refs.length ? "border-transparent py-0.5 text-[10px] text-transparent hover:border-slate-200 hover:text-slate-400" : "border-slate-200 py-2 text-xs text-slate-400 dark:border-slate-700"
          }`}
        >
          {refs.length ? "drop here to add at the end" : emptyText}
        </div>
      </div>
    );
  };

  const sectionHeader = (title: string, isSel: boolean, onSelect: () => void, dragItem: DragItem, controls: ReactNode) => (
    <div
      draggable
      onDragStart={onDragStart(dragItem)}
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
      className={`mb-2 flex cursor-grab items-center justify-between gap-2 rounded-md px-2 py-1 ${isSel ? "bg-indigo-50 dark:bg-indigo-500/10" : "hover:bg-slate-50 dark:hover:bg-slate-800"}`}
      title="Drag to move the whole section - click to rename"
    >
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">⠿ {title}</p>
      <div className="flex items-center gap-1">{controls}</div>
    </div>
  );

  const smallBtn = (label: string, onClick: () => void, disabled = false, danger = false) => (
    <button
      type="button"
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className={`rounded px-1.5 py-0.5 text-xs ${danger ? "text-rose-500 hover:bg-rose-50" : "text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"} disabled:opacity-30`}
    >
      {label}
    </button>
  );

  const docName = template?.documents.find((d) => d.id === docId)?.name ?? "";

  return (
    <AppShell title="Screen Designer" subtitle="Lay out a document's job screen: sections, their order, where each field sits, and the product card - layout only. Changes save straight away.">
      <div className={fullScreen ? "fixed inset-0 z-40 overflow-auto bg-slate-50 p-4 dark:bg-slate-950" : ""}>
      {inOnboarding && <OnboardingSteps current={5} groupId={groupId} />}
      {/* Pickers */}
      <Card className="mb-4 flex flex-wrap items-end gap-4 p-4">
        {inOnboarding && (
          <div className="pb-2">
            <OnboardingCustomer name={template?.name} groupId={groupId} />
          </div>
        )}
        <label className={`flex flex-col gap-1 text-xs font-medium text-slate-500 ${inOnboarding ? "hidden" : ""}`}>
          Template
          <select
            value={groupId}
            onChange={(e) => setSelection(e.target.value, "")}
            className="min-w-[14rem] rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
          >
            <option value="">Pick a template…</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}{t.documents.some((d) => d.has_layout) ? " ●" : ""}
              </option>
            ))}
          </select>
        </label>
        {template && (
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-slate-500">Document</span>
            <div className="flex flex-wrap gap-1 rounded-lg border border-slate-200 p-1 dark:border-slate-700">
              {template.documents.map((d) => (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => setSelection(template.id, d.id)}
                  className={`rounded-md px-3 py-1.5 text-sm font-medium ${d.id === docId ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`}
                >
                  {d.name}{d.has_layout ? " ●" : ""}
                </button>
              ))}
            </div>
          </div>
        )}
        {template && (
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
            Preview job
            <select
              value={previewJobId}
              onChange={(e) => setPreviewJobId(e.target.value)}
              className="min-w-[16rem] rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
            >
              {!jobs.length && <option value="">No jobs on this template yet</option>}
              {jobs.map((j) => (
                <option key={j.id} value={j.id}>
                  {j.reference} · {j.created_at ? formatDateTime(j.created_at) : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        {previewJobId && (
          <Link to={`/jobs/${previewJobId}`} target="_blank" className="pb-2 text-sm text-indigo-600 hover:underline">
            Open this job ↗
          </Link>
        )}
        <Button size="sm" variant="ghost" className="mb-1" onClick={() => setFullScreen((v) => !v)}>
          {fullScreen ? "✕ Close full screen" : "⛶ Full screen"}
        </Button>
        <div className="ml-auto pb-2 text-xs text-slate-400">
          {saveState === "saving" && "Saving…"}
          {saveState === "saved" && meta.updated_at && `Saved ${formatDateTime(meta.updated_at)}${meta.updated_by ? ` by ${meta.updated_by}` : ""}`}
          {saveState === "idle" && meta.updated_at && `Last saved ${formatDateTime(meta.updated_at)}${meta.updated_by ? ` by ${meta.updated_by}` : ""}`}
          {saveState === "error" && <span className="text-rose-500">Not saved</span>}
        </div>
      </Card>

      {message && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          {message}
          <button type="button" className="ml-3 text-xs underline" onClick={() => setMessage(null)}>dismiss</button>
        </div>
      )}

      {!template && <Card className="p-8 text-center text-sm text-slate-500">Pick a template to start.</Card>}

      {template && docId && !loading && !layout && (
        <Card className="p-8 text-center">
          <p className="text-base font-semibold text-slate-800 dark:text-slate-100">{docName} uses the default job screen.</p>
          <p className="mt-1 text-sm text-slate-500">Create a layout from what operators see today, then rearrange it here. Nothing changes on the job screen until you do.</p>
          <Button className="mt-4" onClick={() => void createFromCurrent()}>Create layout from current screen</Button>
        </Card>
      )}

      {template && layout && (
        <div className="grid gap-4 xl:grid-cols-[13rem_minmax(0,1fr)_16rem]">
          {/* Left: fields not placed */}
          <Card className="h-fit p-4 lg:sticky lg:top-4">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Not placed</p>
            <p className="mb-2 text-[11px] text-slate-400">Shown under "Other" on the job screen. Drag one into a section.</p>
            <div className="flex flex-col gap-1">
              {unplaced.length === 0 && <p className="text-xs text-emerald-600">Every field is placed.</p>}
              {unplaced.map((f) => (
                <div
                  key={f.ref}
                  draggable
                  onDragStart={onDragStart({ type: "field", ref: f.ref })}
                  onClick={() => setSelected({ kind: "field", ref: f.ref })}
                  className={`cursor-grab rounded-md border px-2 py-1 text-xs ${selected?.kind === "field" && selected.ref === f.ref ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10" : "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100"}`}
                >
                  ⚠ {fieldName(f, f.ref)} <span className="text-[10px] text-slate-400">{f.place === "card" ? "product line" : ""}</span>
                </div>
              ))}
            </div>
            {jobLevelOffTab.length > 0 && (
              <>
                <p className="mt-4 text-xs font-semibold uppercase tracking-wider text-slate-500">Job-level fields</p>
                <p className="mb-2 text-[11px] text-slate-400">Not on this tab today. Drag one into a section to show it here.</p>
                <div className="flex flex-col gap-1">
                  {jobLevelOffTab.map((f) => (
                    <div
                      key={f.ref}
                      draggable
                      onDragStart={onDragStart({ type: "field", ref: f.ref })}
                      onClick={() => setSelected({ kind: "field", ref: f.ref })}
                      className="cursor-grab rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 dark:border-slate-700 dark:text-slate-300"
                    >
                      {fieldName(f, f.ref)}
                    </div>
                  ))}
                </div>
              </>
            )}
          </Card>

          {/* Centre: the screen, as the operator sees it - Document Preview beside Extracted Data */}
          <div onClick={() => setSelected(null)} className={`grid min-w-0 items-start gap-4 ${showDoc && (previewJobDoc || designSample) ? "lg:grid-cols-2" : ""}`}>
            {showDoc && !previewJob && designSample && (
              <Card className="h-fit min-w-0 p-5 lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-auto">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-lg font-semibold text-indigo-700 dark:text-indigo-300">Document Preview</p>
                  {designSample.page_count > 1 && (
                    <div className="flex items-center gap-1 text-xs text-slate-500">
                      <button type="button" disabled={docPage <= 1} onClick={(e) => { e.stopPropagation(); setDocPage((p) => p - 1); }} className="rounded px-2 py-1 hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-slate-800">‹</button>
                      page {docPage} of {designSample.page_count}
                      <button type="button" disabled={docPage >= designSample.page_count} onClick={(e) => { e.stopPropagation(); setDocPage((p) => p + 1); }} className="rounded px-2 py-1 hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-slate-800">›</button>
                    </div>
                  )}
                </div>
                <p className="mb-3 text-xs text-slate-400">Onboarding sample · {designSample.file_name} - no job on this template yet</p>
                <SamplePagePreview workbookId={designSample.workbookId} docId={designSample.docId} page={Math.min(docPage, designSample.page_count || 1)} />
              </Card>
            )}
            {showDoc && previewJobDoc && previewJob && (
              <Card className="h-fit min-w-0 p-5 lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-auto">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <p className="text-lg font-semibold text-indigo-700 dark:text-indigo-300">Document Preview</p>
                  {previewJobDoc.page_count > 1 && (
                    <div className="flex items-center gap-1 text-xs text-slate-500">
                      <button type="button" disabled={docPage <= 1} onClick={(e) => { e.stopPropagation(); setDocPage((p) => p - 1); }} className="rounded px-2 py-1 hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-slate-800">‹</button>
                      page {docPage} of {previewJobDoc.page_count}
                      <button type="button" disabled={docPage >= previewJobDoc.page_count} onClick={(e) => { e.stopPropagation(); setDocPage((p) => p + 1); }} className="rounded px-2 py-1 hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-slate-800">›</button>
                    </div>
                  )}
                </div>
                <JobPagePreview jobId={previewJob.id} jobDocumentId={previewJobDoc.id} page={Math.min(docPage, previewJobDoc.page_count || 1)} />
              </Card>
            )}
            <Card className="min-w-0 p-5">
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                <p className="text-lg font-semibold text-indigo-700 dark:text-indigo-300">Extracted Data</p>
                <div className="flex items-center gap-2">
                  {(previewJobDoc || designSample) && (
                    <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); setShowDoc((v) => !v); }}>
                      {showDoc ? "Hide document" : "Show document"}
                    </Button>
                  )}
                  <Button size="sm" variant="secondary" onClick={(e) => { e.stopPropagation(); addSection(); }}>+ Add section</Button>
                </div>
              </div>
              <p className="mb-2 text-xs text-slate-500">
                {docName} · as the operator sees it, with {previewJob ? "the preview job's values" : design ? "the onboarding values (Step 2 box reading, else the ERP Excel value)" : "the preview job's values"}. Drag fields and section headings; double-click a label to rename it.
              </p>
              <ColourKeyPreview />
              {unplaced.length > 0 && (
                <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
                  <span className="font-semibold">{unplaced.length} field{unplaced.length === 1 ? " is" : "s are"} not on the screen</span>
                  {" - the operator will not see "}{unplaced.length === 1 ? "it" : "them"}: {unplaced.slice(0, 6).map((f) => f.display_label || f.label_name).join(", ")}
                  {unplaced.length > 6 ? ` and ${unplaced.length - 6} more` : ""}. Drag {unplaced.length === 1 ? "it" : "them"} in from "Not placed" on the left.
                </div>
              )}
              {layout.sections.map((s, i) => (
                <div
                  key={s.id}
                  onDragOver={allowDrop(`sec:${s.id}`)}
                  onDrop={onDropSection(s.id)}
                  className={`mt-4 rounded-xl border p-2 ${dragOver === `sec:${s.id}` ? "border-indigo-300" : "border-transparent hover:border-slate-200 dark:hover:border-slate-700"}`}
                >
                  {sectionHeader(
                    s.title,
                    selected?.kind === "section" && selected.id === s.id,
                    () => setSelected({ kind: "section", id: s.id }),
                    { type: "section", id: s.id },
                    <>
                      {smallBtn("↑", () => void persist({ ...layout, sections: moveInList(layout.sections, s.id, layout.sections[i - 1]?.id ?? null) }), i === 0)}
                      {smallBtn("↓", () => void persist({ ...layout, sections: moveInList(layout.sections, s.id, layout.sections[i + 2]?.id ?? null) }), i === layout.sections.length - 1)}
                    </>,
                  )}
                  {fieldGrid(s.fields, { container: "section", id: s.id }, "Empty section - drop fields here")}
                </div>
              ))}
              <div onDragOver={allowDrop("sec:end")} onDrop={onDropSection(null)} className={`h-3 ${dragOver === "sec:end" ? "bg-indigo-100" : ""}`} />

              {/* Product card */}
              {(layout.product_card.fields.length > 0 || layout.product_card.groups.length > 0 || fields.some((f) => f.place === "card")) && (
                <div className="mt-6 rounded-xl border border-slate-200 p-3 dark:border-slate-700">
                  <div className="mb-2 flex items-center justify-between">
                    <p className="text-xs font-semibold text-slate-500">PRODUCT CARD · Product 1 of the preview job</p>
                    <Button size="sm" variant="secondary" onClick={(e) => { e.stopPropagation(); addGroup(); }}>+ Add group</Button>
                  </div>
                  {fieldGrid(layout.product_card.fields, { container: "card" }, "Drop product-line fields here")}
                  {layout.product_card.groups.map((g, i) => (
                    <div
                      key={g.id}
                      onDragOver={allowDrop(`grp:${g.id}`)}
                      onDrop={onDropGroup(g.id)}
                      className="mt-3 rounded-md border border-indigo-100 bg-indigo-50/40 p-3 dark:border-indigo-500/20 dark:bg-indigo-500/5"
                    >
                      {sectionHeader(
                        g.title,
                        selected?.kind === "group" && selected.id === g.id,
                        () => setSelected({ kind: "group", id: g.id }),
                        { type: "group", id: g.id },
                        <>
                          {smallBtn("↑", () => void persist({ ...layout, product_card: { ...layout.product_card, groups: moveInList(layout.product_card.groups, g.id, layout.product_card.groups[i - 1]?.id ?? null) } }), i === 0)}
                          {smallBtn("↓", () => void persist({ ...layout, product_card: { ...layout.product_card, groups: moveInList(layout.product_card.groups, g.id, layout.product_card.groups[i + 2]?.id ?? null) } }), i === layout.product_card.groups.length - 1)}
                        </>,
                      )}
                      {fieldGrid(g.fields, { container: "group", id: g.id }, "Empty group - drop product-line fields here")}
                    </div>
                  ))}
                </div>
              )}
            </Card>
            <div className="mt-3 flex justify-end">
              <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); void removeLayout(); }}>
                Remove layout (back to default screen)
              </Button>
            </div>
          </div>

          {/* Right: properties */}
          <Card className="h-fit p-4 lg:sticky lg:top-4">
            <PropertiesPanel
              selected={selected}
              layout={layout}
              fieldByRef={fieldByRef}
              onLayout={(next) => void persist(next)}
              onFieldSaved={(ref, patch) => setFields((fs) => fs.map((f) => (f.ref === ref ? { ...f, ...patch } : f)))}
              onDeselect={() => setSelected(null)}
              onError={setMessage}
              groupId={groupId}
            />
          </Card>
        </div>
      )}
      {loading && <Card className="p-8 text-center text-sm text-slate-500">Loading…</Card>}
      {inOnboarding && groupId && (
        <div className="mt-4 flex justify-end gap-2">
          <Link to={`/super-admin/onboarding/field-config?customer=${groupId}`} className="rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800">← Step 4 Field Configuration</Link>
          <Link to={`/super-admin/onboarding/template-settings?customer=${groupId}`} className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700">Next: Template Settings &amp; Go Live →</Link>
        </div>
      )}
      </div>
    </AppShell>
  );
}

function PropertiesPanel({
  selected, layout, fieldByRef, onLayout, onFieldSaved, onDeselect, onError, groupId,
}: {
  selected: Selection;
  layout: ScreenLayout;
  fieldByRef: Map<string, DesignerField>;
  onLayout: (next: ScreenLayout) => void;
  onFieldSaved: (ref: string, patch: Partial<DesignerField>) => void;
  onDeselect: () => void;
  onError: (msg: string) => void;
  groupId: string;
}) {
  if (!selected) {
    return (
      <div className="text-sm text-slate-500">
        <p className="mb-2 font-semibold text-slate-700 dark:text-slate-200">Properties</p>
        <p>Click a field, a section heading or a product-card group to edit it.</p>
        <ul className="mt-3 list-disc space-y-1 pl-4 text-xs text-slate-400">
          <li>Drag fields between sections, or onto another field to put it before that one.</li>
          <li>Drag a section heading to move the whole section.</li>
          <li>Every change saves straight away.</li>
        </ul>
      </div>
    );
  }

  if (selected.kind === "section" || selected.kind === "group") {
    const isSection = selected.kind === "section";
    const list = isSection ? layout.sections : layout.product_card.groups;
    const item = list.find((x) => x.id === selected.id);
    if (!item) return null;
    const rename = (title: string) => {
      const clean = title.trim();
      if (!clean || clean === item.title) return;
      if (list.some((x) => x.id !== item.id && x.title.toLowerCase() === clean.toLowerCase())) {
        onError(`There is already a ${isSection ? "section" : "group"} called '${clean}'.`);
        return;
      }
      const renamed = list.map((x) => (x.id === item.id ? { ...x, title: clean } : x));
      onLayout(isSection ? { ...layout, sections: renamed as ScreenLayout["sections"] }
        : { ...layout, product_card: { ...layout.product_card, groups: renamed } });
    };
    const remove = () => {
      if (!window.confirm(`Delete "${item.title}"? Its ${item.fields.length} field(s) go back to "Not placed".`)) return;
      onLayout(isSection ? { ...layout, sections: layout.sections.filter((x) => x.id !== item.id) }
        : { ...layout, product_card: { ...layout.product_card, groups: layout.product_card.groups.filter((x) => x.id !== item.id) } });
      onDeselect();
    };
    return (
      <div className="flex flex-col gap-3 text-sm">
        <p className="font-semibold text-slate-700 dark:text-slate-200">{isSection ? "Section" : "Product-card group"}</p>
        <label className="flex flex-col gap-1 text-xs text-slate-500">
          Name
          <input
            key={item.id + item.title}
            defaultValue={item.title}
            onBlur={(e) => rename(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
            className="rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
          />
        </label>
        <p className="text-xs text-slate-400">{item.fields.length} field(s)</p>
        <Button size="sm" variant="danger" onClick={remove}>Delete {isSection ? "section" : "group"}</Button>
      </div>
    );
  }

  const field = fieldByRef.get(selected.ref);
  if (!field) return <p className="text-sm text-slate-500">This field no longer exists.</p>;
  return (
    <FieldProperties
      key={field.ref}
      field={field}
      layout={layout}
      onLayout={onLayout}
      onFieldSaved={onFieldSaved}
      onError={onError}
      groupId={groupId}
    />
  );
}

function FieldProperties({
  field, layout, onLayout, onFieldSaved, onError, groupId,
}: {
  field: DesignerField;
  layout: ScreenLayout;
  onLayout: (next: ScreenLayout) => void;
  onFieldSaved: (ref: string, patch: Partial<DesignerField>) => void;
  onError: (msg: string) => void;
  groupId: string;
}) {
  const [saving, setSaving] = useState(false);
  const save = async (patch: designerApi.FieldPatch, local: Partial<DesignerField>) => {
    setSaving(true);
    try {
      await designerApi.patchField(field, patch);
      onFieldSaved(field.ref, local);
    } catch {
      onError(`Could not save ${field.display_label || field.label_name}.`);
    } finally {
      setSaving(false);
    }
  };

  const inLayout = layout.sections.some((s) => s.fields.includes(field.ref))
    || layout.product_card.fields.includes(field.ref)
    || layout.product_card.groups.some((g) => g.fields.includes(field.ref));


  return (
    <div className="flex flex-col gap-3 text-sm">
      <div>
        <p className="font-semibold text-slate-700 dark:text-slate-200">Field</p>
        <p className="font-mono text-[11px] text-slate-400">
          {field.label_name}
          <Link to={`/super-admin/onboarding/field-config?customer=${groupId}`} className="ml-2 font-sans text-indigo-600 hover:underline">rename in Step 4</Link>
        </p>
        <p className="text-[11px] text-slate-400">
          {field.source === "custom" ? `Job-level field (${field.kind})` : field.place === "card" ? "One per product line" : "Read off this document"}
        </p>
      </div>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Label on the screen
        <input
          defaultValue={field.display_label ?? ""}
          placeholder={field.label_name}
          onBlur={(e) => {
            const v = e.target.value.trim();
            if (v !== (field.display_label ?? "")) void save({ display_label: v || null }, { display_label: v || null });
          }}
          onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
          className="rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
        />
      </label>
      <div className="flex flex-col gap-1 text-xs text-slate-500">
        Width
        <div className="flex gap-1">
          {(["quarter", "third", "half", "full"] as const).map((w) => {
            const current = field.full_width ? "full" : field.quarter_width ? "quarter" : field.third_width ? "third" : "half";
            const patch = { full_width: w === "full", quarter_width: w === "quarter", third_width: w === "third" };
            return (
              <button
                key={w}
                type="button"
                onClick={() => current !== w && void save(patch, patch)}
                className={`flex-1 rounded-md border px-2 py-1 text-xs ${current === w ? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" : "border-slate-300 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}
              >
                {w === "full" ? "Full row" : w === "half" ? "Half row" : w === "third" ? "Third" : "Quarter"}
              </button>
            );
          })}
        </div>
      </div>
      <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
        <input type="checkbox" checked={field.multiline} onChange={(e) => void save({ multiline: e.target.checked }, { multiline: e.target.checked })} />
        Multi-line box (addresses, long text)
      </label>
      {/* Behaviour belongs to Step 4 (one owner per setting): shown here, changed there. */}
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-2.5 text-xs dark:border-slate-700 dark:bg-slate-800/40">
        <p className="mb-1 font-medium text-slate-600 dark:text-slate-300">Behaviour</p>
        <div className="flex flex-wrap gap-1">
          <span className={`rounded-full px-2 py-0.5 ${field.mandatory ? "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300" : "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300"}`}>
            {field.mandatory ? "Mandatory" : "Optional"}
          </span>
          <span className="rounded-full bg-slate-200 px-2 py-0.5 text-slate-600 dark:bg-slate-700 dark:text-slate-300">
            {field.fixed_options?.length ? `Dropdown · ${field.fixed_options.length} choices` : "Text box"}
          </span>
        </div>
        <Link to={`/super-admin/onboarding/field-config?customer=${groupId}`} className="mt-1.5 inline-block font-medium text-indigo-600 hover:underline dark:text-indigo-300">
          Change mandatory / choices in Step 4 Field Configuration →
        </Link>
      </div>
      {inLayout && (
        <Button size="sm" variant="secondary" onClick={() => onLayout(withoutRef(layout, field.ref))}>
          Take out of the layout
        </Button>
      )}
      <p className="text-[11px] text-slate-400">{saving ? "Saving…" : "The Screen Designer is layout only: label on the screen, width and multi-line are saved on the field. How a field behaves is Step 4."}</p>
    </div>
  );
}

/** The operator screen's colour key, so the preview reads the same way. */
function ColourKeyPreview() {
  const items: { cls: string; label: string }[] = [
    { cls: "border-sky-200 bg-sky-50 dark:border-sky-500/30 dark:bg-sky-500/10", label: "read off a document" },
    { cls: "border-emerald-200 bg-emerald-50 dark:border-emerald-500/30 dark:bg-emerald-500/10", label: "from the reference sheet" },
    { cls: "border-violet-200 bg-violet-50 dark:border-violet-500/30 dark:bg-violet-500/10", label: "worked out from a document" },
    { cls: "border-slate-200 bg-slate-50 dark:border-slate-600 dark:bg-slate-800/50", label: "fixed / operator" },
    { cls: "border-rose-300 bg-rose-50/60 dark:border-rose-500/40 dark:bg-rose-500/10", label: "blank - nothing found" },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
      {items.map((i) => (
        <span key={i.label} className="flex items-center gap-1.5">
          <span className={`h-3 w-3 rounded-sm border ${i.cls}`} />
          {i.label}
        </span>
      ))}
    </div>
  );
}

/** One page of the preview job's document, as on the operator's Document Preview. */
function JobPagePreview({ jobId, jobDocumentId, page }: { jobId: string; jobDocumentId: string; page: number }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let made: string | null = null;
    let cancelled = false;
    setUrl(null);
    setFailed(false);
    jobsApi.jobDocPageUrl(jobId, jobDocumentId, page)
      .then((u) => { if (cancelled) URL.revokeObjectURL(u); else { made = u; setUrl(u); } })
      .catch(() => !cancelled && setFailed(true));
    return () => { cancelled = true; if (made) URL.revokeObjectURL(made); };
  }, [jobId, jobDocumentId, page]);
  if (failed) return <p className="text-sm text-slate-500">Could not load this page.</p>;
  if (!url) return <p className="text-sm text-slate-400">Loading page…</p>;
  return <img src={url} alt={`Page ${page}`} className="block w-full rounded-lg border border-slate-200 dark:border-slate-700" />;
}

/** One page of the onboarding sample (Step 0) - the designer's document before any job exists. */
function SamplePagePreview({ workbookId, docId, page }: { workbookId: string; docId: string; page: number }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let made: string | null = null;
    let cancelled = false;
    setUrl(null);
    setFailed(false);
    fetchSamplePageUrl(workbookId, docId, page)
      .then((u) => { if (cancelled) URL.revokeObjectURL(u); else { made = u; setUrl(u); } })
      .catch(() => !cancelled && setFailed(true));
    return () => { cancelled = true; if (made) URL.revokeObjectURL(made); };
  }, [workbookId, docId, page]);
  if (failed) return <p className="text-sm text-slate-500">Could not load this page.</p>;
  if (!url) return <p className="text-sm text-slate-400">Loading page…</p>;
  return <img src={url} alt={`Page ${page}`} className="block w-full rounded-lg border border-slate-200 dark:border-slate-700" />;
}
