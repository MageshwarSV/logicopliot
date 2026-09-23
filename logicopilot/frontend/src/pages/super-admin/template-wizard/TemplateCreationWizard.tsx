import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../../components/AppShell";
import * as reviewsApi from "../../../api/reviews";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Input, Select } from "../../../components/ui/Input";
import { Modal } from "../../../components/ui/Modal";
import { Alert } from "../../../components/ui/Alert";
import * as tenantsApi from "../../../api/tenants";
import * as api from "../../../api/onboarding";
import type { Tenant } from "../../../types/tenant";
import type { CustomField, DemoResult, Mark, TemplateGroupDetail } from "../../../types/onboarding";
import { DOC_TYPES, MARK_COLORS, MODES } from "../../../types/onboarding";
import { MarkCanvas, type DraftBox } from "./MarkCanvas";

const STEPS = ["Declare", "Upload", "Mark & Label", "Prompts", "Demo", "ERP Entry", "Correct"];

function errText(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) {
    const d = err.response?.data?.detail;
    if (typeof d === "string") return d;
  }
  return fallback;
}

/** One sheet being filled, as the wizard holds it while it is edited. */
type SheetPlanUi = {
  sheet: string;
  header_row: number;
  /** job — one row for the whole job; line — one row per line item; fixed — a set block;
   *  invoice — one row per invoice, for a shipment covered by several. */
  scope: "job" | "line" | "fixed" | "invoice";
  headers: { column: string; header: string }[];
  /** column letter -> data field, or one of the generated tokens below */
  map: Record<string, string>;
  /** The saved column objects, kept verbatim, keyed by letter. A mapping can carry things this
   *  screen does not edit — a constant the import always needs, one value per row on a fixed
   *  block, a width limit. Rebuilding the configuration from the dropdowns alone would throw
   *  all of that away the moment somebody pressed Save. */
  raw: Record<string, Record<string, unknown>>;
  warning: string | null;
  open: boolean;
};

// Keys of an Excel configuration this screen actually edits. Anything else on it was put there
// by another part of the system - the sample values a template records for the recorder, for
// instance - and a Save here must hand it straight back rather than drop it.
const OWNED_CFG_KEYS = ["source", "file_name", "sheet", "header_row", "columns", "sheets"];

/** Whatever else is on the saved configuration, so a Save preserves it. */
function keptCfg(cfg: Record<string, unknown> | null | undefined): Record<string, unknown> {
  if (!cfg) return {};
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(cfg)) if (!OWNED_CFG_KEYS.includes(k)) out[k] = v;
  return out;
}

/** What a column carries besides a data field, phrased for whoever is reading the table. */
function constantOf(raw: Record<string, unknown> | undefined): string | null {
  if (!raw) return null;
  const many = raw.values;
  if (Array.isArray(many) && many.length) {
    return many.length === 1 ? `always ${String(many[0])}` : `per row: ${many.join(", ")}`;
  }
  const one = raw.value;
  if (one !== undefined && one !== null && String(one) !== "") return `always ${String(one)}`;
  return null;
}

// Columns the system fills in itself: the serial numbers an ERP import keys its rows on. They
// are not extracted from any document, so they cannot be picked from the field list.
const GENERATED_COLUMNS = [
  { value: "#row", label: "# this sheet's row number (1, 2, 3...)" },
  { value: "#invoice", label: "# the invoice this row belongs to" },
];

const flatten = (v: string) => (v || "").toLowerCase().replace(/[^a-z0-9]/g, "");

export function TemplateCreationWizard() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [step, setStep] = useState(1);
  const [editing, setEditing] = useState(false);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [savedOpen, setSavedOpen] = useState(false);

  // Step 1
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [groupName, setGroupName] = useState("");
  const [mode, setMode] = useState("");
  const [decls, setDecls] = useState([{ name: "", doc_type: "BL", is_required: true }]);

  // The picked tenant's own licensed modes, or every mode when they aren't restricted —
  // same "empty = unrestricted" rule the backend applies.
  const availableModes = useMemo(() => {
    const t = tenants.find((x) => x.id === tenantId);
    return t?.allowed_modes?.length ? t.allowed_modes : [...MODES];
  }, [tenants, tenantId]);

  // A mode picked for a different tenant (or none yet) can be outside this one's licensed
  // list — clear it rather than silently submit something the tenant isn't allowed.
  useEffect(() => {
    if (mode && !availableModes.includes(mode)) setMode("");
  }, [availableModes]); // eslint-disable-line react-hooks/exhaustive-deps

  // Shared
  const [group, setGroup] = useState<TemplateGroupDetail | null>(null);
  const [activeDocId, setActiveDocId] = useState<string>("");

  // Step 2
  const [uploadingId, setUploadingId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);

  // Step 3 — label modal + cross-doc queue
  const [pendingBox, setPendingBox] = useState<DraftBox | null>(null);
  const [labelName, setLabelName] = useState("");
  const [markColor, setMarkColor] = useState("red");
  const [verify, setVerify] = useState(false);
  // Tick: this field must be confirmed by the operator before the ERP entry runs.
  const [askOperator, setAskOperator] = useState(false);
  const [askHint, setAskHint] = useState("");
  // When the operator is asked for this value: must they supply it, or may they skip it?
  const [askRequired, setAskRequired] = useState(true);
  // Tick: this document holds many values for the field (a line-item table).
  const [multiValue, setMultiValue] = useState(false);
  // Tick: the extracted value is looked up in its own reference table (keyed on itself) -
  // a match replaces it, no match returns empty instead of the raw extraction.
  const [targetValue, setTargetValue] = useState(false);
  // Target-value only: match on the same real-world thing despite OCR/formatting noise
  // ("KUEHNE + NAGEL PVT. LTD." vs "KUEHNE+NAGEL") instead of requiring identical text.
  const [fuzzyMatch, setFuzzyMatch] = useState(false);
  const [crossPopup, setCrossPopup] = useState<{ sourceMarkId: string; label: string } | null>(null);
  const [crossTargets, setCrossTargets] = useState<string[]>([]);

  // Step 5 / 6
  const [demo, setDemo] = useState<DemoResult | null>(null);
  const [testDocId, setTestDocId] = useState("");
  const [testResult, setTestResult] = useState<DemoResult | null>(null);
  const [testing, setTesting] = useState(false);
  // ---- step 6: how this customer's data reaches the ERP ----
  // fields — the recorded script types every value into its own box
  // excel  — the ERP takes a bulk import, so the job becomes one workbook the script attaches
  const [entryMode, setEntryMode] = useState<"fields" | "excel">("fields");
  const [excelSource, setExcelSource] = useState<"blank" | "template">("blank");
  // Every sheet inside the uploaded workbook, so another one can be added later without
  // choosing the file again.
  const [wbSheets, setWbSheets] = useState<string[]>([]);
  const [wbUploaded, setWbUploaded] = useState(false);
  // One entry per sheet being filled. A real import workbook has several, at different row
  // counts: GENERAL once per job, ITEMS once per line item.
  const [sheetPlans, setSheetPlans] = useState<SheetPlanUi[]>([]);
  const [blankCols, setBlankCols] = useState<{ header: string; field: string }[]>([
    { header: "", field: "" },
  ]);
  const [excelFileName, setExcelFileName] = useState("import.xlsx");
  const [entrySaving, setEntrySaving] = useState(false);
  const [entrySaved, setEntrySaved] = useState<string | null>(null);
  const [correcting, setCorrecting] = useState<Mark | null>(null);
  const [correctionText, setCorrectionText] = useState("");
  // Prompts step — inline editing of a field's prompt/anchors/label
  const [editMarkId, setEditMarkId] = useState<string | null>(null);

  // The customer's reference sheet, and which per-line fields read from it.
  const [refSheet, setRefSheet] = useState<api.ReferenceSheet | null>(null);
  const [refColumns, setRefColumns] = useState<string[]>([]);
  const [refBusy, setRefBusy] = useState(false);
  const [refError, setRefError] = useState<string | null>(null);
  const perLineFields = (group?.custom_fields ?? []).filter((c) => c.per_row);

  useEffect(() => {
    if (!group) return;
    let cancelled = false;
    (async () => {
      try {
        // The status reply carries the headings, so opening this screen costs one call, not two.
        const sheet = await api.getReferenceSheet(group.id);
        if (cancelled) return;
        setRefSheet(sheet);
        if (sheet.processing) {
          // A parse kicked off by an upload elsewhere (or a self-heal, see the status route)
          // is already in flight — pick it up instead of showing a stale/empty state.
          void pollRefSheet();
        } else {
          setRefColumns(sheet.columns ?? []);
        }
      } catch {
        // A customer with no sheet is the normal case, not an error worth showing.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [group?.id]);

  async function refreshGroup() {
    if (!group) return;
    setGroup(await api.getGroup(group.id));
  }

  async function pollRefSheet() {
    if (!group) return;
    // A real master (9.8 MB / 22,000 rows) takes 25-30+ seconds to parse in the background -
    // check back periodically instead of blocking on one long request, which is what used to
    // get the server's own worker killed for going quiet too long.
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      const sheet = await api.getReferenceSheet(group.id);
      setRefSheet(sheet);
      if (!sheet.processing) {
        setRefColumns(sheet.columns ?? []);
        if (!sheet.ok && sheet.detail) setRefError(sheet.detail);
        return;
      }
    }
    setRefError("Still processing after two minutes — reopen this screen shortly to check again.");
  }

  async function uploadRef(file: File) {
    if (!group) return;
    setRefBusy(true);
    setRefError(null);
    try {
      const res = await api.uploadReferenceSheet(group.id, file);
      setRefSheet(res);
      // The upload route always parses off-thread now and reports processing:true — poll
      // the status route until it clears.
      await pollRefSheet();
    } catch (err: any) {
      setRefError(errText(err, "That sheet could not be read."));
    } finally {
      setRefBusy(false);
    }
  }

  async function removeRef() {
    if (!group) return;
    setRefBusy(true);
    try {
      await api.deleteReferenceSheet(group.id);
      setRefSheet({ attached: false, materials: 0 });
      setRefColumns([]);
    } catch (err: any) {
      setRefError(errText(err, "The sheet could not be removed."));
    } finally {
      setRefBusy(false);
    }
  }

  /** Turn a per-line field into a lookup, or back into a plain one. */
  async function setLookup(cf: api.CustomField, on: boolean) {
    setRefBusy(true);
    setRefError(null);
    try {
      await api.updateCustomField(cf.id, {
        kind: on ? "lookup" : "hardcoded",
        // The material code read off each invoice line is what a lookup is keyed on; left
        // empty the engine falls back to that anyway, so it is set explicitly for clarity.
        lookup_key_label: on ? "item_material_code" : null,
      });
      await refreshGroup();
    } catch (err: any) {
      setRefError(errText(err, "That field could not be changed."));
    } finally {
      setRefBusy(false);
    }
  }

  async function setLookupColumns(cf: api.CustomField, match?: string, ret?: string) {
    setRefBusy(true);
    try {
      await api.updateCustomField(cf.id, {
        ...(match !== undefined
          ? { lookup_match_columns: match ? [match] : null }
          : {}),
        ...(ret !== undefined ? { lookup_return_column: ret || null } : {}),
      });
      await refreshGroup();
    } catch (err: any) {
      setRefError(errText(err, "That column could not be set."));
    } finally {
      setRefBusy(false);
    }
  }
  const [editLabel, setEditLabel] = useState("");
  const [editPrompt, setEditPrompt] = useState("");
  const [editAnchors, setEditAnchors] = useState<string[]>([]);
  const [newAnchor, setNewAnchor] = useState("");
  const [savingMark, setSavingMark] = useState(false);
  // Custom tag (computed/hardcoded field)
  const [customOpen, setCustomOpen] = useState(false);
  const [cfLabel, setCfLabel] = useState("");
  const [cfKind, setCfKind] = useState<"hardcoded" | "ai">("ai");
  const [cfValue, setCfValue] = useState("");
  const [cfPrompt, setCfPrompt] = useState("");
  const [cfDocs, setCfDocs] = useState<string[]>([]);
  const [cfSaving, setCfSaving] = useState(false);
  const [cfAskOperator, setCfAskOperator] = useState(false);
  const [cfAskHint, setCfAskHint] = useState("");
  const [cfAskRequired, setCfAskRequired] = useState(true);
  // id of the custom tag being edited; null means the modal is creating a new one.
  const [cfEditId, setCfEditId] = useState<string | null>(null);
  // Ask for this value once per invoice line rather than once per job.
  const [cfPerRow, setCfPerRow] = useState(false);
  // kind="ai" only: read every instance straight off the document(s) instead of one value.
  const [cfMultiValue, setCfMultiValue] = useState(false);
  // Cross-verify this custom field against a mark on another document - see
  // CrossDocLink.source_custom_field_id. Ticking it, after save, opens crossFieldPopup to
  // pick which mark(s); mirrors verify/crossPopup for a mark, but the target is picked
  // directly (a custom field's own label rarely matches any mark's).
  const [cfVerify, setCfVerify] = useState(false);
  // The computed value is looked up in its own reference table (keyed on itself) - a match
  // replaces it, no match returns empty instead of the raw computation.
  const [cfTargetValue, setCfTargetValue] = useState(false);
  // Target-value only: same as fuzzyMatch above, for a custom field.
  const [cfFuzzyMatch, setCfFuzzyMatch] = useState(false);
  const [crossFieldPopup, setCrossFieldPopup] = useState<{ customFieldId: string; label: string } | null>(null);
  const [crossFieldTargets, setCrossFieldTargets] = useState<string[]>([]);
  // Custom ruling (decides which documents a job requires)
  const [rulingOpen, setRulingOpen] = useState(false);
  const [rulingText, setRulingText] = useState("");
  const [rulingSaving, setRulingSaving] = useState(false);
  // True when the ruling dialog was raised by pressing Next after the uploads, rather
  // than by the toolbar button — in that case saving (or skipping) continues the wizard.
  const [rulingGate, setRulingGate] = useState(false);

  useEffect(() => {
    const editId = searchParams.get("edit");
    const rev = searchParams.get("review");
    if (editId) {
      // Editing an existing template (e.g. from the inbox): load it and jump
      // straight into Mark & Label — no re-declaring or re-uploading.
      setEditing(true);
      setReviewId(rev);
      api
        .getGroup(editId)
        .then((g) => {
          setGroup(g);
          setActiveDocId(g.documents[0]?.id ?? "");
          setStep(3);
        })
        .catch(() => setError("Failed to load the template to edit."));
    } else {
      tenantsApi
        .listTenants()
        .then((rows) => {
          setTenants(rows);
          if (rows[0]) setTenantId(rows[0].id);
        })
        .catch(() => setError("Failed to load tenants — is the backend running?"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The stepper is clickable once a template exists (created or being edited),
  // so you can jump directly to the stage you need instead of walking every step.
  function goToStep(n: number) {
    if (n === step) return;
    if (group && n >= 2) setStep(n);
    else if (!group && n === 1) setStep(1);
  }

  const activeDoc = useMemo(
    () => group?.documents.find((d) => d.id === activeDocId) ?? null,
    [group, activeDocId],
  );

  async function reloadGroup(id: string) {
    const g = await api.getGroup(id);
    setGroup(g);
    return g;
  }

  function openCustom() {
    setCfEditId(null);
    setCfLabel(""); setCfKind("ai"); setCfValue(""); setCfPrompt(""); setCfAskOperator(false); setCfAskRequired(true); setCfPerRow(false); setCfMultiValue(false); setCfAskHint(""); setCfVerify(false); setCfTargetValue(false); setCfFuzzyMatch(false);
    setCfDocs(group?.documents.map((d) => d.id) ?? []); // default: all documents
    setError(null); setCustomOpen(true);
  }

  /** Open the tag modal pre-filled, to change an existing tag rather than replace it. */
  function editCustom(cf: CustomField) {
    setCfEditId(cf.id);
    setCfLabel(cf.label_name);
    setCfKind(cf.kind === "hardcoded" ? "hardcoded" : "ai");
    setCfValue(cf.hardcoded_value ?? "");
    setCfPrompt(cf.ai_prompt ?? "");
    setCfDocs(cf.source_document_ids?.length ? cf.source_document_ids : group?.documents.map((d) => d.id) ?? []);
    setCfAskOperator(!!cf.ask_operator);
    setCfAskRequired(cf.ask_operator_required !== false);
    setCfPerRow(!!cf.per_row);
    setCfMultiValue(!!cf.multi_value_from_document);
    setCfAskHint(cf.ask_operator_hint ?? "");
    setCfVerify(!!cf.verify_with_other_document);
    setCfTargetValue(!!cf.is_target_value);
    setCfFuzzyMatch(!!cf.fuzzy_match);
    setError(null);
    setCustomOpen(true);
  }

  async function saveCustomField() {
    if (!group) return;
    if (!cfLabel.trim()) { setError("Give the custom tag a label."); return; }
    // A fixed value is only required when the field ISN'T being asked for. Ticking "ask the
    // operator" means the value is not known now — the IGM number has not been filed yet, the
    // master BL has not been issued — and demanding one at template time made that impossible
    // to configure. Left empty it becomes a blank the operator fills in on every job; typed,
    // it becomes a standing default they confirm, which is how the duty notifications work.
    if (cfKind === "hardcoded" && !cfAskOperator && !cfValue.trim()) {
      setError("Enter the fixed value, or tick “Ask the operator” if it is different each job.");
      return;
    }
    if (cfKind === "ai" && !cfPrompt.trim()) { setError("Describe what the AI should compute."); return; }
    setCfSaving(true); setError(null);
    try {
      const payload = {
        label_name: cfLabel.trim(),
        kind: cfKind,
        hardcoded_value: cfKind === "hardcoded" ? cfValue.trim() : null,
        ai_prompt: cfKind === "ai" ? cfPrompt.trim() : null,
        source_document_ids: cfKind === "ai" ? cfDocs : [],
        ask_operator: cfAskOperator,
        ask_operator_required: cfAskOperator ? cfAskRequired : true,
        per_row: cfAskOperator ? cfPerRow : false,
        multi_value_from_document: cfKind === "ai" ? cfMultiValue : false,
        ask_operator_hint: cfAskOperator ? cfAskHint.trim() || null : null,
        is_target_value: cfKind === "ai" ? cfTargetValue : false,
        fuzzy_match: cfKind === "ai" && cfTargetValue ? cfFuzzyMatch : false,
      };
      const saved = cfEditId
        ? await api.updateCustomField(cfEditId, payload)
        : await api.createCustomField(group.id, payload);
      const savedId = cfEditId ?? saved.id;
      if (cfVerify && cfKind === "ai" && savedId) {
        // Flagged for cross-doc: ask which mark(s) on other documents it also matches.
        setCrossFieldPopup({ customFieldId: savedId, label: cfLabel.trim() });
        setCrossFieldTargets([]);
      }
      await reloadGroup(group.id);
      setCustomOpen(false);
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not save the custom tag.");
    } finally {
      setCfSaving(false);
    }
  }

  function openRuling() {
    setRulingText(group?.ruling_prompt ?? "");
    setError(null);
    setRulingOpen(true);
  }

  async function saveRuling() {
    if (!group) return;
    setRulingSaving(true); setError(null);
    try {
      await api.setRuling(group.id, rulingText.trim() || null);
      await reloadGroup(group.id);
      setRulingOpen(false);
      if (rulingGate) {
        setRulingGate(false);
        setStep(3);
      }
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not save the ruling.");
    } finally {
      setRulingSaving(false);
    }
  }

  /** Next, after the sample uploads: offer the ruling before moving on to marking. */
  function nextFromUpload() {
    if (!group?.ruling_prompt) {
      setRulingText(group?.ruling_prompt ?? "");
      setError(null);
      setRulingGate(true);
      setRulingOpen(true);
      return;
    }
    setStep(3);
  }

  function skipRuling() {
    setRulingOpen(false);
    if (rulingGate) {
      setRulingGate(false);
      setStep(3);
    }
  }

  async function removeCustomField(fieldId: string) {
    if (!group) return;
    try {
      await api.deleteCustomField(fieldId);
      await reloadGroup(group.id);
    } catch {
      setError("Could not delete that custom tag.");
    }
  }

  // ---- Step 1 ----
  async function submitDeclare() {
    setError(null);
    const documents = decls
      .map((d) => ({ name: d.name.trim(), doc_type: d.doc_type, is_required: d.is_required !== false }))
      .filter((d) => d.name);
    if (!tenantId) return setError("Pick a customer/tenant first.");
    if (!mode) return setError("Pick which mode this template is for.");
    if (!groupName.trim()) return setError("Give this template set a name.");
    if (documents.length === 0) return setError("Add at least one document.");
    setBusy(true);
    try {
      const g = await api.createGroup({ tenant_id: tenantId, name: groupName.trim(), mode, documents });
      setGroup(g);
      setActiveDocId(g.documents[0]?.id ?? "");
      setStep(2);
    } catch (err) {
      setError(errText(err, "Could not create the template set."));
    } finally {
      setBusy(false);
    }
  }

  // ---- Step 2 ----
  async function handleUpload(docId: string, file: File | null | undefined) {
    if (!file || !group) return;
    if (!/\.(pdf|png|jpe?g)$/i.test(file.name)) {
      setError("Only PDF, PNG, or JPG files are allowed.");
      return;
    }
    setError(null);
    setUploadingId(docId);
    try {
      await api.uploadDocument(docId, file);
      await reloadGroup(group.id);
    } catch (err) {
      setError(errText(err, "Upload failed."));
    } finally {
      setUploadingId(null);
    }
  }

  const allUploaded = !!group && group.documents.every((d) => d.is_uploaded);

  // ---- Step 3 ----
  function openLabelModal(box: DraftBox) {
    setPendingBox(box);
    setLabelName("");
    setVerify(false);
    setAskOperator(false);
    setAskRequired(true);
    setAskHint("");
    setMultiValue(false);
    setTargetValue(false);
    setFuzzyMatch(false);
    setMarkColor("red");
  }

  async function saveMark() {
    if (!pendingBox || !activeDoc || !group) return;
    setBusy(true);
    setError(null);
    try {
      const mark = await api.createMark(activeDoc.id, {
        label_name: labelName.trim(),
        page_number: 1,
        x: pendingBox.x,
        y: pendingBox.y,
        width: pendingBox.width,
        height: pendingBox.height,
        color: markColor,
        verify_with_other_document: verify,
        ask_operator: askOperator,
        ask_operator_required: askOperator ? askRequired : true,
        ask_operator_hint: askOperator ? askHint.trim() || null : null,
        is_multi_value: multiValue,
        is_target_value: targetValue,
        fuzzy_match: targetValue ? fuzzyMatch : false,
      });

      if (verify) {
        // Flagged for cross-doc: ask which other documents it also appears in.
        // (No re-cropping — we just link and pull it by meaning on those docs.)
        setCrossPopup({ sourceMarkId: mark.id, label: mark.label_name });
        setCrossTargets([]);
      }

      setPendingBox(null);
      await reloadGroup(group.id);
    } catch (err) {
      setError(errText(err, "Could not save the mark."));
      setPendingBox(null);
    } finally {
      setBusy(false);
    }
  }

  async function confirmCrossTargets() {
    if (!crossPopup || crossTargets.length === 0) {
      setCrossPopup(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // Link the field to the chosen documents — backend finds it there by meaning.
      await api.linkFieldToDocuments(crossPopup.sourceMarkId, { target_document_ids: crossTargets });
      if (group) await reloadGroup(group.id);
    } catch (err) {
      setError(errText(err, "Could not link the field across documents."));
    } finally {
      setBusy(false);
      setCrossPopup(null);
      setCrossTargets([]);
    }
  }

  async function confirmCrossFieldTargets() {
    if (!crossFieldPopup || crossFieldTargets.length === 0) {
      setCrossFieldPopup(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.linkCustomFieldToMarks(crossFieldPopup.customFieldId, { target_mark_ids: crossFieldTargets });
      if (group) await reloadGroup(group.id);
    } catch (err) {
      setError(errText(err, "Could not link the field to those marks."));
    } finally {
      setBusy(false);
      setCrossFieldPopup(null);
      setCrossFieldTargets([]);
    }
  }

  async function toggleDocRequired(doc: { id: string; is_required?: boolean }) {
    if (!group) return;
    setBusy(true);
    try {
      await api.setDocumentRequired(doc.id, doc.is_required === false);
      await reloadGroup(group.id);
    } catch (err) {
      setError(errText(err, "Could not change mandatory/optional."));
    } finally {
      setBusy(false);
    }
  }

  async function removeMark(markId: string) {
    if (!group) return;
    setBusy(true);
    try {
      await api.deleteMark(markId);
      await reloadGroup(group.id);
    } catch (err) {
      setError(errText(err, "Could not delete the mark."));
    } finally {
      setBusy(false);
    }
  }

  function startEditMark(m: Mark) {
    setEditMarkId(m.id);
    setEditLabel(m.label_name);
    setEditPrompt(m.extraction_prompt ?? "");
    setEditAnchors(m.anchor_variations ?? []);
    setNewAnchor("");
    setError(null);
  }

  function cancelEditMark() {
    setEditMarkId(null);
    setNewAnchor("");
  }

  async function saveEditMark() {
    if (!editMarkId || !group) return;
    setSavingMark(true);
    setError(null);
    try {
      await api.editMark(editMarkId, {
        label_name: editLabel.trim() || undefined,
        extraction_prompt: editPrompt,
        anchor_variations: editAnchors,
      });
      await reloadGroup(group.id);
      setEditMarkId(null);
      setNewAnchor("");
    } catch {
      setError("Could not save the field changes.");
    } finally {
      setSavingMark(false);
    }
  }

  // Load what was SAVED for this customer whenever the group arrives. Without this the step
  // showed its defaults - "Field by field", one blank column - over a live Excel configuration,
  // and pressing Save then overwrote the real one with those defaults.
  useEffect(() => {
    if (!group) return;
    const mode = (group.entry_mode as "fields" | "excel") ?? "fields";
    setEntryMode(mode === "excel" ? "excel" : "fields");
    const cfg = group.excel_config ?? null;
    if (!cfg) return;
    const src = cfg.source === "template" ? "template" : "blank";
    setExcelSource(src);
    setExcelFileName(cfg.file_name || "import.xlsx");
    const cols = cfg.columns ?? [];
    if (src === "template") {
      // Rebuild every sheet's mapping and header list from the saved config alone, so
      // reopening the wizard does not mean uploading the workbook again. A configuration
      // saved before multi-sheet carries one sheet at the top level; read that too.
      const raw = (cfg.sheets && cfg.sheets.length)
        ? cfg.sheets
        : [{ sheet: cfg.sheet, header_row: cfg.header_row, scope: "line", columns: cols }];
      const plans: SheetPlanUi[] = raw.map((sh) => {
        const scols = sh.columns ?? [];
        const map: Record<string, string> = {};
        for (const c of scols) if (c.column) map[c.column] = c.field ?? "";
        const rawCols: Record<string, Record<string, unknown>> = {};
        for (const c of scols) if (c.column) rawCols[c.column] = { ...c };
        return {
          sheet: sh.sheet ?? "",
          header_row: sh.header_row ?? 1,
          scope: sh.scope === "line" ? "line" : sh.scope === "fixed" ? "fixed"
            : sh.scope === "invoice" ? "invoice" : "job",
          headers: scols.map((c) => ({ column: c.column ?? "", header: c.header ?? "" })),
          map,
          raw: rawCols,
          warning: null,
          open: raw.length === 1,
        };
      });
      setSheetPlans(plans);
      setWbUploaded(true);
      // The config records the sheets being FILLED, not every sheet in the file. Ask the
      // server for the real list so another one can still be added.
      setWbSheets(plans.map((p) => p.sheet));
      api.readExcelTemplateHeaders(group.id, plans[0]?.sheet ?? null, plans[0]?.header_row ?? 1)
        .then((info) => { if (info.sheets?.length) setWbSheets(info.sheets); })
        .catch(() => { /* the file may be gone; the saved mapping still shows */ });
    } else {
      setBlankCols(cols.length
        ? cols.map((c) => ({ header: c.header ?? "", field: c.field ?? "" }))
        : [{ header: "", field: "" }]);
    }
  }, [group]);

  const allMarks = useMemo(() => group?.documents.flatMap((d) => d.marks) ?? [], [group]);

  // ---- step 6 helpers: one sheet of the import workbook at a time ----
  /** Turn a sheet the server just read into a plan, guessing what can be guessed. */
  function planFrom(info: api.ExcelTemplateInfo & { warning?: string }): SheetPlanUi {
    const headers = info.headers ?? [];
    // A sheet carrying an item serial is a line-item sheet. Everything else gets one row.
    const scope: "job" | "line" =
      headers.some((h) => flatten(h.header) === "itemsrno") ? "line" : "job";
    const map: Record<string, string> = {};
    for (const h of headers) {
      const f = flatten(h.header);
      if (!f || !h.column) continue;
      if (f === "itemsrno") { map[h.column] = "#row"; continue; }
      if (f === "invsrno") { map[h.column] = scope === "line" ? "#invoice" : "#row"; continue; }
      const hit = allMarks.find((m) => flatten(m.label_name) === f)
        ?? (group?.custom_fields ?? []).find((c) => flatten(c.label_name) === f);
      if (hit) map[h.column] = hit.label_name;
    }
    return {
      sheet: info.sheet ?? "",
      header_row: info.header_row ?? 1,
      scope,
      headers,
      map,
      raw: {},
      warning: info.warning ?? null,
      open: true,
    };
  }

  async function addSheetPlan(name: string) {
    if (!group || !name) return;
    setError(null);
    try {
      const info = await api.readExcelTemplateHeaders(group.id, name, 1);
      setSheetPlans((ps) => (ps.some((p) => p.sheet === name) ? ps : [...ps, planFrom(info)]));
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? `Could not read the sheet ${name}.`);
    }
  }

  /** Re-read one sheet under a different heading row, keeping the mapping that still fits. */
  async function reReadSheet(index: number, headerRow: number) {
    const plan = sheetPlans[index];
    if (!group || !plan) return;
    setError(null);
    try {
      const info = await api.readExcelTemplateHeaders(group.id, plan.sheet, headerRow);
      const headers = info.headers ?? [];
      setSheetPlans((ps) => ps.map((p, j) => (j === index ? {
        ...p,
        header_row: info.header_row ?? headerRow,
        headers,
        warning: info.warning ?? null,
        map: Object.fromEntries(
          Object.entries(p.map).filter(([col]) => headers.some((h) => h.column === col)),
        ),
        raw: Object.fromEntries(
          Object.entries(p.raw ?? {}).filter(([col]) => headers.some((h) => h.column === col)),
        ),
      } : p)));
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not read that heading row.");
    }
  }

  // A column carrying a constant puts data in the file just as a mapped field does, so it
  // counts. Without this the step reported 84 of 298 for a mapping that fills 131.
  const mappedCount = (p: SheetPlanUi) =>
    p.headers.filter((h) =>
      (p.map[h.column] || "").trim() !== "" || constantOf(p.raw?.[h.column]) !== null).length;
  const docNameByMarkId = useMemo(() => {
    const map = new Map<string, string>();
    group?.documents.forEach((d) => d.marks.forEach((m) => map.set(m.id, d.name)));
    return map;
  }, [group]);

  // ---- Step 5 ----
  async function runDemo(docId: string) {
    setBusy(true);
    setError(null);
    try {
      setDemo(await api.demoExtract(docId));
    } catch (err) {
      setError(errText(err, "Demo run failed."));
    } finally {
      setBusy(false);
    }
  }

  async function runTestExtract(file: File | null | undefined) {
    if (!file || !testDocId) return;
    if (!/\.(pdf|png|jpe?g)$/i.test(file.name)) {
      setError("Only PDF, PNG, or JPG files are allowed.");
      return;
    }
    setError(null);
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await api.testExtract(testDocId, file));
    } catch (err) {
      setError(errText(err, "Test extraction failed."));
    } finally {
      setTesting(false);
    }
  }

  async function finish() {
    if (!group) return;
    setFinalizing(true);
    setError(null);
    try {
      await api.finalizeGroup(group.id);
      // If we came from an inbox change-request, mark it resolved.
      if (reviewId) {
        try {
          await reviewsApi.resolveReview(reviewId);
        } catch {
          /* non-fatal */
        }
      }
      setSavedOpen(true);
    } catch (err) {
      setError(errText(err, "Could not save the template set."));
    } finally {
      setFinalizing(false);
    }
  }

  // ---- Step 6 ----
  async function applyCorrection() {
    if (!correcting || !group) return;
    setBusy(true);
    setError(null);
    try {
      await api.correctMark(correcting.id, correctionText.trim());
      await reloadGroup(group.id);
      setCorrecting(null);
      setCorrectionText("");
    } catch (err) {
      setError(errText(err, "Could not apply the correction."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell title="Customer with Template Creation" subtitle="Onboard a customer's documents step by step.">
      {/* Stepper */}
      <div className="mb-8 flex flex-wrap items-center gap-2">
        {STEPS.map((label, i) => {
          const n = i + 1;
          const state = n === step ? "current" : n < step ? "done" : "todo";
          const clickable = (group && n >= 2) || (!group && n === 1);
          return (
            <div key={label} className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => goToStep(n)}
                disabled={!clickable}
                title={clickable ? `Go to ${label}` : undefined}
                className={`flex items-center gap-2 rounded-lg px-1 py-0.5 ${clickable ? "cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-800" : "cursor-default"}`}
              >
                <span
                  className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold ${
                    state === "current"
                      ? "bg-indigo-600 text-white"
                      : state === "done"
                        ? "bg-emerald-500 text-white"
                        : "bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-400"
                  }`}
                >
                  {n}
                </span>
                <span className={`text-sm ${n === step ? "font-semibold text-slate-900 dark:text-slate-50" : "text-slate-500"}`}>
                  {label}
                </span>
              </button>
              {n < STEPS.length && <span className="mx-1 text-slate-300 dark:text-slate-700">→</span>}
            </div>
          );
        })}
      </div>

      {editing && (
        <div className="mb-4 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-2 text-sm text-indigo-800 dark:border-indigo-500/20 dark:bg-indigo-500/10 dark:text-indigo-300">
          Editing an existing template — click any step above to jump straight there. No need to re-upload or re-declare. Changes save when you click <b>Done</b>.
        </div>
      )}

      {error && (
        <div className="mb-6">
          <Alert>{error}</Alert>
        </div>
      )}

      {/* STEP 1 — Declare */}
      {step === 1 && (
        <Card className="max-w-2xl p-6">
          <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Declare the documents</h2>
          <div className="flex flex-col gap-4">
            <Select label="Customer / Tenant" value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
              {tenants.length === 0 && <option value="">No tenants yet</option>}
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </Select>
            <Select label="What is this template for?" value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="">Pick a mode…</option>
              {availableModes.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </Select>
            {tenants.find((t) => t.id === tenantId)?.allowed_modes?.length ? (
              <p className="-mt-2 text-xs text-slate-400 dark:text-slate-500">
                Only showing modes this client is licensed for.
              </p>
            ) : null}
            <Input label="Template set name" value={groupName} onChange={(e) => setGroupName(e.target.value)} placeholder="Import Shipment Set" />
            <div>
              <p className="mb-2 text-sm font-medium text-slate-700 dark:text-slate-300">Documents in this set</p>
              <div className="flex flex-col gap-2">
                {decls.map((d, i) => (
                  <div key={i} className="flex items-end gap-2">
                    <div className="flex-1">
                      <Input
                        label={`Document ${i + 1}`}
                        value={d.name}
                        onChange={(e) => setDecls((arr) => arr.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
                        placeholder="e.g. BL, Invoice, Packing List"
                      />
                    </div>
                    <select
                      value={d.doc_type}
                      onChange={(e) => setDecls((arr) => arr.map((x, j) => (j === i ? { ...x, doc_type: e.target.value } : x)))}
                      className="mb-0.5 rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm dark:border-slate-700 dark:bg-slate-900"
                    >
                      {DOC_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                    <label className="mb-0.5 flex items-center gap-1.5 whitespace-nowrap text-xs text-slate-600 dark:text-slate-300">
                      <input
                        type="checkbox"
                        checked={d.is_required !== false}
                        onChange={(e) => setDecls((arr) => arr.map((x, j) => (j === i ? { ...x, is_required: e.target.checked } : x)))}
                        className="h-3.5 w-3.5 rounded border-slate-300"
                      />
                      Mandatory
                    </label>
                    {decls.length > 1 && (
                      <Button variant="ghost" size="sm" onClick={() => setDecls((arr) => arr.filter((_, j) => j !== i))}>
                        Remove
                      </Button>
                    )}
                  </div>
                ))}
              </div>
              <p className="mt-1.5 text-xs text-slate-400">
                Unchecked = optional — the job can move past Document Capture without it.
              </p>
              <Button variant="secondary" size="sm" className="mt-2" onClick={() => setDecls((arr) => [...arr, { name: "", doc_type: "Custom", is_required: true }])}>
                + Add document
              </Button>
            </div>
          </div>
          <div className="mt-6 flex justify-end">
            <Button onClick={submitDeclare} isLoading={busy}>Next: Upload →</Button>
          </div>
        </Card>
      )}

      {/* STEP 2 — Upload */}
      {step === 2 && group && (
        <Card className="max-w-2xl p-6">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">Upload a sample of each document</h2>
          <p className="mb-5 mt-1 text-sm text-slate-500">
            Add one example file per document so the AI can learn its layout. PDF, PNG, or JPG.
          </p>
          <div className="flex flex-col gap-4">
            {group.documents.map((d) => {
              const isUploading = uploadingId === d.id;
              return (
                <div key={d.id}>
                  <div className="mb-1.5 flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{d.name}</span>
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                      {d.doc_type}
                    </span>
                  </div>

                  {d.is_uploaded ? (
                    <div className="flex items-center justify-between rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 dark:border-emerald-500/20 dark:bg-emerald-500/10">
                      <span className="flex items-center gap-2 text-sm font-medium text-emerald-700 dark:text-emerald-300">
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                        Uploaded · {d.page_count} page{d.page_count === 1 ? "" : "s"}
                      </span>
                      <label className="cursor-pointer text-xs font-medium text-indigo-600 hover:underline">
                        Replace
                        <input
                          type="file"
                          accept=".pdf,.png,.jpg,.jpeg"
                          className="hidden"
                          onChange={(e) => handleUpload(d.id, e.target.files?.[0])}
                        />
                      </label>
                    </div>
                  ) : (
                    <label
                      onDragOver={(e) => { e.preventDefault(); setDragOverId(d.id); }}
                      onDragLeave={() => setDragOverId(null)}
                      onDrop={(e) => { e.preventDefault(); setDragOverId(null); handleUpload(d.id, e.dataTransfer.files?.[0]); }}
                      className={`flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed px-4 py-6 text-center transition-colors ${
                        dragOverId === d.id
                          ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10"
                          : "border-slate-300 bg-slate-50 hover:border-indigo-400 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800"
                      }`}
                    >
                      {isUploading ? (
                        <span className="flex items-center gap-2 text-sm font-medium text-indigo-600">
                          <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                          </svg>
                          Uploading &amp; rendering pages…
                        </span>
                      ) : (
                        <>
                          <svg className="h-6 w-6 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M7 16a4 4 0 01-.88-7.9A5 5 0 1115.9 6H16a5 5 0 011 9.9M12 12v8m0-8l-3 3m3-3l3 3" />
                          </svg>
                          <span className="text-sm font-medium text-slate-700 dark:text-slate-200">
                            Click to choose a file <span className="text-slate-400">or drag it here</span>
                          </span>
                          <span className="text-xs text-slate-400">PDF, PNG, or JPG</span>
                        </>
                      )}
                      <input
                        type="file"
                        accept=".pdf,.png,.jpg,.jpeg"
                        className="hidden"
                        disabled={isUploading}
                        onChange={(e) => handleUpload(d.id, e.target.files?.[0])}
                      />
                    </label>
                  )}
                </div>
              );
            })}
          </div>
          <div className="mt-6 flex items-center justify-between">
            <Button variant="secondary" onClick={() => setStep(1)}>← Back</Button>
            <div className="flex items-center gap-3">
              {!allUploaded && <span className="text-xs text-slate-400">Upload all documents to continue</span>}
              <Button onClick={nextFromUpload} disabled={!allUploaded}>Next: Mark &amp; Label →</Button>
            </div>
          </div>
        </Card>
      )}

      {/* STEP 3 — Mark & Label */}
      {step === 3 && group && activeDoc && (
        <div>
          <div className="mb-4 flex items-center justify-between gap-2">
            <div className="flex flex-wrap gap-2">
              {group.documents.map((d) => (
                <button
                  key={d.id}
                  onClick={() => setActiveDocId(d.id)}
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
                    d.id === activeDocId
                      ? "bg-indigo-600 text-white"
                      : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"
                  }`}
                >
                  {d.name} <span className="text-xs opacity-70">({d.marks.length})</span>
                  {d.is_required === false && <span className="ml-1 text-xs opacity-70">· optional</span>}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => toggleDocRequired(activeDoc)}
                disabled={busy}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                  activeDoc.is_required === false
                    ? "bg-amber-100 text-amber-800 dark:bg-amber-500/10 dark:text-amber-400"
                    : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                }`}
                title={`${activeDoc.name}: click to make this ${activeDoc.is_required === false ? "mandatory" : "optional"}`}
              >
                {activeDoc.is_required === false ? "Optional" : "Mandatory"}
              </button>
              <Button size="sm" variant={group.ruling_prompt ? "primary" : "secondary"} onClick={openRuling}>
                ⚖️ Custom ruling{group.ruling_prompt ? " ✓" : ""}
              </Button>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <MarkCanvas
                documentId={activeDoc.id}
                page={1}
                marks={activeDoc.marks}
                labelForMark={(m) => m.label_name}
                onDraw={openLabelModal}
                disabled={busy}
              />
              <p className="mt-2 text-xs text-slate-500">Drag a box over a value (e.g. the BL number itself, not its caption).</p>
            </div>
            <div>
              <h3 className="mb-2 text-sm font-semibold text-slate-900 dark:text-slate-50">Marks on {activeDoc.name}</h3>
              <div className="flex max-h-[60vh] flex-col gap-2 overflow-y-auto pr-1">
                {activeDoc.marks.length === 0 && <p className="text-sm text-slate-400">No marks yet.</p>}
                {activeDoc.marks.map((m) => (
                  <div key={m.id} className="rounded-lg border border-slate-200 p-2.5 dark:border-slate-800">
                    <div className="flex items-center gap-2">
                      <span className="h-3 w-3 rounded-full" style={{ backgroundColor: MARK_COLORS[m.color] }} />
                      <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{m.label_name}</span>
                      {m.verify_with_other_document && (
                        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">cross-doc</span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-slate-500">Anchor: {m.detected_anchor ?? "—"}</p>
                    <button onClick={() => removeMark(m.id)} className="mt-1 text-xs text-rose-600 hover:underline">Delete</button>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="mt-6 flex justify-between">
            <Button variant="secondary" onClick={() => setStep(2)}>← Back</Button>
            <Button onClick={() => setStep(4)} disabled={allMarks.length === 0}>Next: Prompts →</Button>
          </div>
        </div>
      )}

      {/* STEP 4 — Prompts review */}
      {step === 4 && group && (
        <div>
          <p className="mb-4 text-sm text-slate-500">Each field is saved as a prompt + anchor variations that tell the AI how the value appears across documents. Click <b>Edit</b> to fine-tune any field.</p>
          {/* The customer's own reference sheet. Some values are on none of the documents and
              are not a judgement either - the CTH a part is declared under is settled long
              before the shipment, and the customer already has the list. Uploading it here
              means a field can look the answer up instead of an operator typing it on every
              line of every job. */}
          <Card className="mb-4 p-4">
            <div className="mb-2 flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">
                  Reference sheet (optional)
                </h3>
                <p className="mt-0.5 text-xs text-slate-500">
                  The customer's own export — part code, description and the code it is declared
                  under. A field can then look a value up instead of anyone typing it.
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <label className="cursor-pointer rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800">
                  {refBusy ? "Reading…" : refSheet?.attached ? "Replace" : "Upload sheet"}
                  <input
                    type="file"
                    accept=".xlsx,.xlsm"
                    className="hidden"
                    disabled={refBusy}
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) void uploadRef(f);
                      e.currentTarget.value = "";
                    }}
                  />
                </label>
                {refSheet?.attached && (
                  <Button size="sm" variant="ghost" onClick={() => void removeRef()} disabled={refBusy}>
                    Remove
                  </Button>
                )}
              </div>
            </div>

            {refError && <p className="text-xs text-rose-600 dark:text-rose-400">{refError}</p>}

            {refSheet?.attached && refSheet.processing ? (
              <div className="rounded-md bg-slate-50 p-3 text-xs dark:bg-slate-800/60">
                <p className="font-medium text-slate-700 dark:text-slate-200">
                  {refSheet.file_name} — reading it now, this can take a minute for a large sheet…
                </p>
              </div>
            ) : refSheet?.attached ? (
              <div className="rounded-md bg-slate-50 p-3 text-xs dark:bg-slate-800/60">
                <p className="font-medium text-slate-700 dark:text-slate-200">
                  {refSheet.file_name} — {refSheet.materials.toLocaleString()} usable key(s)
                </p>
                {refSheet.detail && (
                  <p className="mt-1 text-amber-700 dark:text-amber-400">{refSheet.detail}</p>
                )}
                {refColumns.length > 0 && (
                  <p className="mt-1 text-slate-500">
                    Columns: {refColumns.join(" · ")}
                  </p>
                )}
                {refSheet.sample && refSheet.sample.length > 0 && (
                  <table className="mt-2 w-full text-left font-mono text-[11px]">
                    <tbody>
                      {refSheet.sample.map((r: { material: string; cth: string }) => (
                        <tr key={r.material}>
                          <td className="pr-3 text-slate-500">{r.material}</td>
                          <td className="text-slate-700 dark:text-slate-200">{r.cth}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                <p className="mt-2 text-slate-500">
                  A part number that is not on the sheet, or a description the sheet gives more
                  than one code for, is left blank for the operator — never guessed.
                </p>
              </div>
            ) : (
              <p className="text-xs text-slate-500">
                No sheet attached. Fields that look values up will come out blank.
              </p>
            )}

            {/* Point each per-line field at the columns it should use. Only per-line fields are
                offered: a lookup answers a question about one product, not about the job. */}
            {refSheet?.attached && perLineFields.length > 0 && (
              <div className="mt-3 border-t border-slate-200 pt-3 dark:border-slate-700">
                <p className="mb-2 text-xs font-medium text-slate-600 dark:text-slate-300">
                  Which fields come from this sheet?
                </p>
                <div className="flex flex-col gap-2">
                  {perLineFields.map((cf) => {
                    const on = cf.kind === "lookup";
                    return (
                      <div key={cf.id} className="flex flex-wrap items-center gap-2 text-xs">
                        <label className="flex min-w-[11rem] items-center gap-2">
                          <input
                            type="checkbox"
                            checked={on}
                            disabled={refBusy}
                            onChange={(e) => void setLookup(cf, e.target.checked)}
                          />
                          <span className="font-medium">{cf.label_name}</span>
                        </label>
                        {on && (
                          <>
                            <span className="text-slate-500">match</span>
                            <select
                              className="rounded border border-slate-300 bg-white px-1.5 py-1 dark:border-slate-600 dark:bg-slate-900"
                              value={cf.lookup_match_columns?.[0] ?? ""}
                              onChange={(e) => void setLookupColumns(cf, e.target.value, undefined)}
                            >
                              <option value="">(by heading)</option>
                              {refColumns.map((c) => (
                                <option key={c} value={c}>{c}</option>
                              ))}
                            </select>
                            <span className="text-slate-500">give me</span>
                            <select
                              className="rounded border border-slate-300 bg-white px-1.5 py-1 dark:border-slate-600 dark:bg-slate-900"
                              value={cf.lookup_return_column ?? ""}
                              onChange={(e) => void setLookupColumns(cf, undefined, e.target.value)}
                            >
                              <option value="">(by heading)</option>
                              {refColumns.map((c) => (
                                <option key={c} value={c}>{c}</option>
                              ))}
                            </select>
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
                <p className="mt-2 text-[11px] text-slate-500">
                  Left as "(by heading)" the sheet is read the usual way — matched on a column
                  called Material or a description, returning one called Comm./imp. code no.
                </p>
              </div>
            )}
          </Card>

          <div className="flex flex-col gap-3">
            {allMarks.map((m) => {
              const isEditing = editMarkId === m.id;
              return (
              <Card key={m.id} className="p-4">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-slate-900 dark:text-slate-50">{m.label_name}</span>
                    <span className="text-xs text-slate-400">on {docNameByMarkId.get(m.id)}</span>
                  </div>
                  {!isEditing ? (
                    <Button size="sm" variant="secondary" onClick={() => startEditMark(m)}>Edit</Button>
                  ) : (
                    <div className="flex gap-2">
                      <Button size="sm" variant="ghost" onClick={cancelEditMark}>Cancel</Button>
                      <Button size="sm" onClick={saveEditMark} isLoading={savingMark}>Save</Button>
                    </div>
                  )}
                </div>
                <p className="text-xs text-slate-500">Detected anchor: {m.detected_anchor ?? "—"} · example: {m.example_value ?? "—"}</p>

                {!isEditing ? (
                  <>
                    {m.anchor_variations && m.anchor_variations.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {m.anchor_variations.map((v) => (
                          <span key={v} className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">{v}</span>
                        ))}
                      </div>
                    )}
                    <p className="mt-2 text-xs text-slate-600 dark:text-slate-400"><b>Prompt:</b> {m.extraction_prompt}</p>
                  </>
                ) : (
                  <div className="mt-3 space-y-3">
                    <Input label="Field name" value={editLabel} onChange={(e) => setEditLabel(e.target.value)} />
                    <div>
                      <p className="mb-1 text-xs font-medium text-slate-600 dark:text-slate-300">Anchor variations (labels this field can appear under)</p>
                      <div className="flex flex-wrap gap-1.5">
                        {editAnchors.map((v, i) => (
                          <span key={`${v}-${i}`} className="flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                            {v}
                            <button type="button" onClick={() => setEditAnchors((a) => a.filter((_, j) => j !== i))} className="text-rose-500 hover:text-rose-600">×</button>
                          </span>
                        ))}
                      </div>
                      <div className="mt-2 flex gap-2">
                        <input
                          value={newAnchor}
                          onChange={(e) => setNewAnchor(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") { e.preventDefault(); const v = newAnchor.trim(); if (v) { setEditAnchors((a) => [...a, v]); setNewAnchor(""); } }
                          }}
                          placeholder="Add an anchor and press Enter"
                          className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                        />
                        <Button size="sm" variant="secondary" type="button" onClick={() => { const v = newAnchor.trim(); if (v) { setEditAnchors((a) => [...a, v]); setNewAnchor(""); } }}>Add</Button>
                      </div>
                    </div>
                    <div>
                      <p className="mb-1 text-xs font-medium text-slate-600 dark:text-slate-300">Extraction prompt</p>
                      <textarea
                        value={editPrompt}
                        onChange={(e) => setEditPrompt(e.target.value)}
                        rows={4}
                        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                      />
                    </div>
                  </div>
                )}
              </Card>
              );
            })}
          </div>

          {/* Custom tags — hardcoded or AI-computed fields (not cropped) */}
          <div className="mt-6">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">✨ Custom tags</h3>
              <Button size="sm" variant="secondary" onClick={openCustom}>+ Custom tag</Button>
            </div>
            <p className="mb-2 text-xs text-slate-500">Fields that aren't cropped — a fixed value, or one the AI computes from the documents you choose (e.g. a calculation across the invoice + packing list).</p>
            {(group.custom_fields ?? []).length === 0 ? (
              <p className="text-xs text-slate-400">No custom tags yet.</p>
            ) : (
              <div className="flex flex-col gap-2">
                {(group.custom_fields ?? []).map((cf) => (
                  <div key={cf.id} className="flex items-start gap-3 rounded-lg border border-violet-200 bg-violet-50/40 px-3 py-2 text-sm dark:border-violet-500/20 dark:bg-violet-500/5">
                    <span className="rounded bg-violet-100 px-1.5 py-0.5 text-[11px] font-medium text-violet-700 dark:bg-violet-500/20 dark:text-violet-300">{cf.kind === "hardcoded" ? "fixed" : "AI"}</span>
                    <span className="min-w-0 flex-1">
                      <span className="font-medium text-slate-800 dark:text-slate-100">{cf.label_name}</span>
                      {cf.verify_with_other_document && (
                        <span className="ml-1.5 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">cross-doc</span>
                      )}
                      <span className="block text-xs text-slate-500">{cf.kind === "hardcoded" ? `= ${cf.hardcoded_value}` : cf.ai_prompt}</span>
                    </span>
                    <button onClick={() => editCustom(cf)} className="text-xs font-medium text-indigo-600 hover:underline">edit</button>
                    <button onClick={() => removeCustomField(cf.id)} className="text-xs text-rose-600 hover:underline">delete</button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="mt-6 flex justify-between">
            <Button variant="secondary" onClick={() => setStep(3)}>← Back</Button>
            <Button onClick={() => { setStep(5); setTestDocId(group.documents[0]?.id ?? ""); if (group.documents[0]) runDemo(group.documents[0].id); }}>Next: Demo →</Button>
          </div>
        </div>
      )}

      {/* STEP 5 — Demo */}
      {step === 5 && group && (
        <div>
          <p className="mb-4 text-sm text-slate-500">Confirm the config works — first on your reference docs, then on a real unseen document.</p>

          <Card className="mb-6 p-4">
            <h3 className="mb-3 text-sm font-semibold text-slate-900 dark:text-slate-50">Reference documents</h3>
            <div className="mb-3 flex flex-wrap gap-2">
              {group.documents.map((d) => (
                <Button key={d.id} size="sm" variant={demo?.document_id === d.id ? "primary" : "secondary"} onClick={() => runDemo(d.id)} isLoading={busy && demo?.document_id !== d.id}>
                  Run on {d.name}
                </Button>
              ))}
            </div>
            {demo && demo.results.length === 0 && <p className="text-sm text-slate-400">No marks on this document.</p>}
            {demo?.results.map((r) => (
              <div key={r.mark_id} className="border-b border-slate-100 py-2 last:border-0 dark:border-slate-800">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-700 dark:text-slate-300">
                    {r.label_name}
                    {r.extracted_values && (
                      <span className="ml-2 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-medium text-violet-700 dark:bg-violet-500/20 dark:text-violet-300">
                        {r.extracted_values.length} rows
                      </span>
                    )}
                  </span>
                  {!r.extracted_values && (
                    <span className="text-sm text-slate-900 dark:text-slate-100">{r.extracted_value ?? <span className="text-rose-500">not found</span>}</span>
                  )}
                </div>
                {r.extracted_values && (
                  <ol className="mt-1 space-y-0.5 pl-4">
                    {r.extracted_values.map((v, i) => (
                      <li key={i} className="text-xs text-slate-600 dark:text-slate-400">
                        <span className="text-slate-400">{r.label_name} {i + 1}:</span>{" "}
                        <span className="text-slate-900 dark:text-slate-100">{v ?? "—"}</span>
                      </li>
                    ))}
                    {r.extracted_values.length === 0 && <li className="text-xs text-rose-500">no rows found</li>}
                  </ol>
                )}
              </div>
            ))}
          </Card>

          <Card className="p-4">
            <h3 className="mb-1 text-sm font-semibold text-slate-900 dark:text-slate-50">Test on an unseen document</h3>
            <p className="mb-3 text-xs text-slate-500">Upload a different real document to check the prompts still pull the right values.</p>
            <div className="mb-3 flex flex-wrap items-end gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-400">Which document?</label>
                <select
                  value={testDocId}
                  onChange={(e) => { setTestDocId(e.target.value); setTestResult(null); }}
                  className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900"
                >
                  {group.documents.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </div>
              <label className="inline-flex cursor-pointer items-center rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500">
                {testing ? "Extracting…" : "Upload test file"}
                <input type="file" accept=".pdf,.png,.jpg,.jpeg" className="hidden" disabled={testing} onChange={(e) => runTestExtract(e.target.files?.[0])} />
              </label>
            </div>
            {testResult && (
              <>
                <div className="mb-3 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
                  {testResult.results.map((r) => (
                    <div key={r.mark_id} className="flex items-center justify-between border-b border-slate-100 py-1.5 last:border-0 dark:border-slate-800/60">
                      <span className="text-sm text-slate-600 dark:text-slate-300">{r.label_name}</span>
                      <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{r.extracted_value ?? <span className="text-rose-500">not found</span>}</span>
                    </div>
                  ))}
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-sm text-slate-500">Do these look right?</span>
                  <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">✓ Looks correct</span>
                  <Button size="sm" variant="secondary" onClick={() => setStep(7)}>✗ Something's wrong — fix a field</Button>
                </div>
              </>
            )}
          </Card>

          <div className="mt-6 flex justify-between">
            <Button variant="secondary" onClick={() => setStep(4)}>← Back</Button>
            <Button onClick={() => setStep(6)}>Next: ERP Entry →</Button>
          </div>
        </div>
      )}

      {/* STEP 6 — Correct / train */}
      {/* ---------- 6. ERP Entry - field by field, or a bulk spreadsheet import ---------- */}
      {step === 6 && group && (
        <div className="mx-auto max-w-4xl">
          <Card>
            <h3 className="mb-1 text-lg font-semibold text-slate-900 dark:text-slate-50">
              How does this data reach the ERP?
            </h3>
            <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">
              Some ERPs want every value typed into its own box. Others take a bulk import: the
              script reaches an upload screen, hands over a workbook and presses a button. Either
              way the ERP script still logs in and navigates there &mdash; this only decides what
              happens once it arrives.
            </p>

            <div className="grid gap-3 sm:grid-cols-2">
              <button
                type="button"
                onClick={() => setEntryMode("fields")}
                className={`rounded-lg border p-4 text-left ${entryMode === "fields"
                  ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10"
                  : "border-slate-200 dark:border-slate-700"}`}
              >
                <span className="block font-semibold text-slate-900 dark:text-slate-50">
                  Field by field
                </span>
                <span className="mt-1 block text-xs text-slate-500">
                  The script types each value into its own box. No spreadsheet involved.
                </span>
              </button>
              <button
                type="button"
                onClick={() => setEntryMode("excel")}
                className={`rounded-lg border p-4 text-left ${entryMode === "excel"
                  ? "border-emerald-500 bg-emerald-50 dark:bg-emerald-500/10"
                  : "border-slate-200 dark:border-slate-700"}`}
              >
                <span className="block font-semibold text-slate-900 dark:text-slate-50">
                  Import a spreadsheet
                </span>
                <span className="mt-1 block text-xs text-slate-500">
                  The job becomes one workbook, and the script attaches it on the upload screen.
                </span>
              </button>
            </div>

            {entryMode === "excel" && (
              <div className="mt-6 space-y-5">
                <div>
                  <p className="mb-2 text-sm font-medium text-slate-700 dark:text-slate-200">
                    Where does the shape of the sheet come from?
                  </p>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <button
                      type="button"
                      onClick={() => setExcelSource("blank")}
                      className={`rounded-lg border p-3 text-left text-sm ${excelSource === "blank"
                        ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10"
                        : "border-slate-200 dark:border-slate-700"}`}
                    >
                      <span className="block font-medium">Create a blank sheet</span>
                      <span className="mt-0.5 block text-xs text-slate-500">
                        You name the columns; the file is built for you.
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setExcelSource("template")}
                      className={`rounded-lg border p-3 text-left text-sm ${excelSource === "template"
                        ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10"
                        : "border-slate-200 dark:border-slate-700"}`}
                    >
                      <span className="block font-medium">Use the template I already have</span>
                      <span className="mt-0.5 block text-xs text-slate-500">
                        Upload it &mdash; the headings are read and kept exactly as they are.
                      </span>
                    </button>
                  </div>
                </div>

                {excelSource === "template" && (
                  <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-700">
                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-200">
                      The import workbook the ERP accepts (.xlsx)
                    </label>
                    <input
                      type="file"
                      accept=".xlsx,.xlsm"
                      className="mt-2 block w-full text-sm"
                      onChange={async (e) => {
                        const f = e.target.files?.[0];
                        if (!f || !group) return;
                        setError(null);
                        try {
                          const info = await api.uploadExcelTemplate(group.id, f);
                          setWbSheets(info.sheets ?? []);
                          setWbUploaded(true);
                          setExcelFileName(info.file_name || f.name);
                          // Start with the first sheet - the server already read it. The rest
                          // are added below, because a workbook of nineteen sheets rarely
                          // needs more than a handful filled.
                          setSheetPlans([planFrom(info)]);
                        } catch (err: any) {
                          setError(err?.response?.data?.detail ?? "That workbook could not be read.");
                        }
                      }}
                    />
                    {wbUploaded && (
                      <p className="mt-2 text-xs text-slate-500">
                        {/* Reopening the wizard shows "No file chosen" next to the browse button
                            even though the workbook is stored on the server. Say so plainly, or
                            it reads as though it has to be uploaded again. */}
                        <span className="font-medium text-slate-600 dark:text-slate-300">
                          {excelFileName}
                        </span>{" "}
                        is on the server &mdash; choose a file only to replace it.{" "}
                        {wbSheets.length} sheet(s) inside; add each sheet the ERP reads, and every
                        other sheet is handed over exactly as it came.
                      </p>
                    )}
                  </div>
                )}

                {excelSource === "template" && wbUploaded && (
                  <div className="space-y-3">
                    <p className="text-sm font-medium text-slate-700 dark:text-slate-200">
                      Which sheets does the ERP read, and what feeds each column?
                    </p>

                    {sheetPlans.map((plan, i) => (
                      <div
                        key={plan.sheet || i}
                        className="rounded-lg border border-slate-200 dark:border-slate-700"
                      >
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-slate-100 px-3 py-2 dark:border-slate-800">
                          <button
                            type="button"
                            onClick={() => setSheetPlans((ps) =>
                              ps.map((p, j) => (j === i ? { ...p, open: !p.open } : p)))}
                            className="text-sm font-semibold text-slate-900 dark:text-slate-50"
                          >
                            {plan.open ? "\u25be" : "\u25b8"} {plan.sheet || "(unnamed sheet)"}
                          </button>
                          <span className="text-xs text-slate-500">
                            {mappedCount(plan)} of {plan.headers.length} columns mapped
                          </span>
                          <div className="ml-auto flex flex-wrap items-center gap-2">
                            <span className="text-xs text-slate-500">This sheet gets</span>
                            <select
                              value={plan.scope}
                              onChange={(e) => setSheetPlans((ps) => ps.map((p, j) =>
                                (j === i ? { ...p, scope: e.target.value as "job" | "line" | "fixed" | "invoice" } : p)))}
                              className="rounded border border-slate-300 bg-white px-2 py-1 text-xs dark:border-slate-600 dark:bg-slate-900"
                            >
                              <option value="job">one row for the whole job</option>
                              <option value="line">one row per line item</option>
                              <option value="invoice">one row per invoice</option>
                              <option value="fixed">a set block of rows</option>
                            </select>
                            <span className="text-xs text-slate-500">Headings in row</span>
                            <input
                              type="number"
                              min={1}
                              value={plan.header_row}
                              onChange={(e) => reReadSheet(i, Math.max(1, Number(e.target.value) || 1))}
                              className="w-16 rounded border border-slate-300 bg-white px-2 py-1 text-xs dark:border-slate-600 dark:bg-slate-900"
                            />
                            <button
                              type="button"
                              onClick={() => setSheetPlans((ps) => ps.filter((_, j) => j !== i))}
                              className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                            >
                              remove
                            </button>
                          </div>
                        </div>

                        {plan.warning && (
                          <p className="border-b border-slate-100 bg-amber-50 px-3 py-1.5 text-xs text-amber-800 dark:border-slate-800 dark:bg-amber-500/10 dark:text-amber-300">
                            {plan.warning}
                          </p>
                        )}

                        {plan.open && (
                          <div className="max-h-96 overflow-y-auto">
                            <table className="w-full text-sm">
                              <thead className="sticky top-0 bg-slate-50 text-left text-xs uppercase text-slate-500 dark:bg-slate-800">
                                <tr>
                                  <th className="px-3 py-2">Col</th>
                                  <th className="px-3 py-2">Heading in the sheet</th>
                                  <th className="px-3 py-2">Data field</th>
                                </tr>
                              </thead>
                              <tbody>
                                {plan.headers.map((h) => (
                                  <tr key={h.column} className="border-t border-slate-100 dark:border-slate-800">
                                    <td className="px-3 py-2 font-mono text-xs text-slate-500">{h.column}</td>
                                    <td className="px-3 py-2">
                                      {h.header || <span className="text-slate-400">(blank &mdash; a spacer column)</span>}
                                    </td>
                                    <td className="px-3 py-2">
                                      <select
                                        value={plan.map[h.column] ?? ""}
                                        onChange={(e) => setSheetPlans((ps) => ps.map((p, j) =>
                                          (j === i
                                            ? { ...p, map: { ...p.map, [h.column]: e.target.value } }
                                            : p)))}
                                        className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-900"
                                      >
                                        <option value="">
                                          {constantOf(plan.raw?.[h.column])
                                            ?? "\u2014 leave empty \u2014"}
                                        </option>
                                        <optgroup label="Filled in for you">
                                          {GENERATED_COLUMNS.map((g) => (
                                            <option key={g.value} value={g.value}>{g.label}</option>
                                          ))}
                                        </optgroup>
                                        <optgroup label="From the documents">
                                          {allMarks.map((m) => (
                                            <option key={m.id} value={m.label_name}>
                                              {m.label_name}{m.is_multi_value ? " (per line item)" : ""}
                                            </option>
                                          ))}
                                        </optgroup>
                                        {(group.custom_fields ?? []).length > 0 && (
                                          <optgroup label="Custom fields">
                                            {(group.custom_fields ?? []).map((c) => (
                                              <option key={c.id} value={c.label_name}>
                                                {c.label_name}{c.per_row ? " (per line item)" : ""}
                                              </option>
                                            ))}
                                          </optgroup>
                                        )}
                                      </select>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    ))}

                    <select
                      value=""
                      onChange={(e) => { const v = e.target.value; if (v) addSheetPlan(v); }}
                      className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
                    >
                      <option value="">+ Add a sheet to fill</option>
                      {wbSheets
                        .filter((sh) => !sheetPlans.some((p) => p.sheet === sh))
                        .map((sh) => <option key={sh} value={sh}>{sh}</option>)}
                    </select>
                  </div>
                )}

                {excelSource === "blank" && (
                  <div>
                    <p className="mb-2 text-sm font-medium text-slate-700 dark:text-slate-200">
                      The columns to create, in order
                    </p>
                    <div className="space-y-2">
                      {blankCols.map((c, i) => (
                        <div key={i} className="flex items-center gap-2">
                          <input
                            value={c.header}
                            onChange={(e) => setBlankCols((a) => a.map((x, j) => (j === i ? { ...x, header: e.target.value } : x)))}
                            placeholder="Column heading the ERP expects"
                            className="flex-1 rounded border border-slate-300 bg-white px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
                          />
                          <select
                            value={c.field}
                            onChange={(e) => setBlankCols((a) => a.map((x, j) => (j === i ? { ...x, field: e.target.value } : x)))}
                            className="flex-1 rounded border border-slate-300 bg-white px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
                          >
                            <option value="">&mdash; pick a data field &mdash;</option>
                            {allMarks.map((m) => (
                              <option key={m.id} value={m.label_name}>{m.label_name}</option>
                            ))}
                            {(group.custom_fields ?? []).map((cf) => (
                              <option key={cf.id} value={cf.label_name}>{cf.label_name}</option>
                            ))}
                          </select>
                          <button
                            type="button"
                            onClick={() => setBlankCols((a) => a.filter((_, j) => j !== i))}
                            className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                          >
                            remove
                          </button>
                        </div>
                      ))}
                    </div>
                    <Button size="sm" variant="secondary" className="mt-2"
                      onClick={() => setBlankCols((a) => [...a, { header: "", field: "" }])}>
                      + Add column
                    </Button>
                  </div>
                )}

                <div>
                  <label className="block text-sm font-medium text-slate-700 dark:text-slate-200">
                    File name handed to the ERP
                  </label>
                  <input
                    value={excelFileName}
                    onChange={(e) => setExcelFileName(e.target.value)}
                    className="mt-1 w-64 rounded border border-slate-300 bg-white px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
                  />
                  <p className="mt-1 text-xs text-slate-500">
                    The ERP script attaches this with an <span className="font-mono">upload</span> step.
                    Record that step by pressing the Browse button on the ERP and picking any file;
                    at run time each job attaches its own workbook instead.
                  </p>
                </div>

                {entrySaved && (
                  <p className="rounded bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
                    {entrySaved}
                  </p>
                )}
              </div>
            )}
          </Card>

          <div className="mt-6 flex justify-between">
            <Button variant="secondary" onClick={() => setStep(5)}>&larr; Back</Button>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                isLoading={entrySaving}
                onClick={async () => {
                  if (!group) return;
                  setError(null);
                  setEntrySaving(true);
                  try {
                    let cfg: api.ExcelConfig | null = null;
                    if (entryMode === "excel") {
                      // Guard the trap: "use my own template" with no sheet added means the
                      // blank-sheet rows would be saved as a template config. Those have no
                      // spreadsheet column letters, so a job would attach a workbook with none
                      // of its data written into it.
                      if (excelSource === "template" && !sheetPlans.length) {
                        setError(
                          "Upload the ERP's workbook and add at least one sheet - its column "
                          + "layout is what the data is written into. Without it nothing would "
                          + "be filled in.",
                        );
                        setEntrySaving(false);
                        return;
                      }
                      if (excelSource === "template") {
                        cfg = {
                          ...keptCfg(group.excel_config as Record<string, unknown> | null),
                          source: "template",
                          file_name: excelFileName,
                          columns: [],
                          sheets: sheetPlans.map((p) => ({
                            sheet: p.sheet,
                            header_row: p.header_row,
                            scope: p.scope,
                            // Spread what was saved FIRST, so a constant, a per-row list or a
                            // width limit this screen does not edit survives the save.
                            columns: p.headers.map((h) => ({
                              ...(p.raw?.[h.column] ?? {}),
                              column: h.column,
                              header: h.header,
                              field: p.map[h.column] ?? "",
                            })),
                          })),
                        };
                      } else {
                        cfg = {
                          ...keptCfg(group.excel_config as Record<string, unknown> | null),
                          source: "blank",
                          sheet: "Import",
                          // We write the headings ourselves, so they sit on row 1.
                          header_row: 1,
                          file_name: excelFileName,
                          columns: blankCols
                            .filter((c) => c.header.trim() || c.field)
                            .map((c) => ({ header: c.header.trim() || c.field, field: c.field })),
                        };
                      }
                    }
                    const updated = await api.setEntryMode(group.id, entryMode, cfg);
                    setGroup(updated);
                    setEntrySaved(
                      entryMode === "excel"
                        ? `Saved. Every job for this customer will produce ${excelFileName}`
                          + (excelSource === "template" && sheetPlans.length
                            ? `, filling ${sheetPlans.map((p) => p.sheet).join(", ")}.`
                            : ".")
                        : "Saved. The script will type each value into its own box.",
                    );
                  } catch (err: any) {
                    setError(err?.response?.data?.detail ?? "Could not save the entry mode.");
                  } finally {
                    setEntrySaving(false);
                  }
                }}
              >
                Save entry mode
              </Button>
              <Button onClick={() => setStep(7)}>Next: Correct &rarr;</Button>
            </div>
          </div>
        </div>
      )}

      {step === 7 && group && (
        <div>
          <p className="mb-4 text-sm text-slate-500">Click a field to add a correction. It refines that field's prompt, then re-run the demo to recheck.</p>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card className="p-4">
              <h3 className="mb-2 text-sm font-semibold text-slate-900 dark:text-slate-50">Fields</h3>
              {allMarks.map((m) => (
                <button
                  key={m.id}
                  onClick={() => { setCorrecting(m); setCorrectionText(m.correction_prompt ?? ""); }}
                  className={`mb-1 flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm ${
                    correcting?.id === m.id ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10" : "hover:bg-slate-100 dark:hover:bg-slate-800"
                  }`}
                >
                  <span>{m.label_name} <span className="text-xs text-slate-400">({docNameByMarkId.get(m.id)})</span></span>
                  {m.correction_prompt && <span className="text-xs text-emerald-600">trained</span>}
                </button>
              ))}
            </Card>
            <Card className="p-4">
              {!correcting && <p className="text-sm text-slate-400">Select a field to correct.</p>}
              {correcting && (
                <div className="flex flex-col gap-3">
                  <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">Correct “{correcting.label_name}”</h3>
                  <p className="text-xs text-slate-500">Current prompt: {correcting.extraction_prompt}</p>
                  <textarea
                    value={correctionText}
                    onChange={(e) => setCorrectionText(e.target.value)}
                    rows={4}
                    placeholder="e.g. The value is always a 4-letter carrier code followed by 7 digits. Ignore any 'COPY' watermark."
                    className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900"
                  />
                  <div className="flex gap-2">
                    <Button size="sm" onClick={applyCorrection} isLoading={busy} disabled={!correctionText.trim()}>Apply &amp; retrain</Button>
                    <Button size="sm" variant="secondary" onClick={() => group && runDemo(correcting.document_id)}>Recheck</Button>
                  </div>
                </div>
              )}
            </Card>
          </div>
          <div className="mt-6 flex justify-between">
            <Button variant="secondary" onClick={() => setStep(6)}>← Back</Button>
            <Button onClick={finish} isLoading={finalizing}>Done — save template set</Button>
          </div>
        </div>
      )}

      {/* Saved confirmation */}
      <Modal open={savedOpen} onClose={() => setSavedOpen(false)} title="Template set saved ✓">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            “{group?.name}” has been saved and is now available to run in <b>Jobs</b>.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setSavedOpen(false)}>Close</Button>
            <Button onClick={() => navigate("/jobs")}>Go to Jobs</Button>
          </div>
        </div>
      </Modal>

      {/* Label modal (after drawing a box) */}
      <Modal open={pendingBox !== null} onClose={() => setPendingBox(null)} title="Name this field">
        <div className="flex flex-col gap-4">
          <Input
            label="Field name"
            value={labelName}
            onChange={(e) => setLabelName(e.target.value)}
            placeholder="e.g. bl_number"
          />
          <div>
            <p className="mb-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">Box colour</p>
            <div className="flex gap-2">
              {Object.entries(MARK_COLORS).map(([key, hex]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setMarkColor(key)}
                  className={`h-6 w-6 rounded-full border-2 ${markColor === key ? "border-slate-900 dark:border-white" : "border-transparent"}`}
                  style={{ backgroundColor: hex }}
                />
              ))}
            </div>
          </div>
          <label className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm dark:border-amber-500/20 dark:bg-amber-500/10">
            <input type="checkbox" className="mt-0.5" checked={verify} onChange={(e) => setVerify(e.target.checked)} />
            <span className="text-amber-800 dark:text-amber-300">
              <b>Present in another document?</b><br />
              <span className="text-xs">Tick to cross-verify this value against your other documents (you'll pick which — no re-cropping needed).</span>
            </span>
          </label>
          <label className="flex items-start gap-2 rounded-lg border border-violet-200 bg-violet-50 p-3 text-sm dark:border-violet-500/20 dark:bg-violet-500/10">
            <input type="checkbox" className="mt-0.5" checked={multiValue} onChange={(e) => setMultiValue(e.target.checked)} />
            <span className="text-violet-800 dark:text-violet-300">
              <b>Multiple values in this document?</b><br />
              <span className="text-xs">Tick when the document lists this field once per row (a line-item table). Extraction reads the whole table and returns every row — value 1, value 2, value 3 and so on — instead of a single value. Fields ticked together stay row-aligned.</span>
            </span>
          </label>
          <label className="flex items-start gap-2 rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm dark:border-sky-500/20 dark:bg-sky-500/10">
            <input type="checkbox" className="mt-0.5" checked={askOperator} onChange={(e) => setAskOperator(e.target.checked)} />
            <span className="text-sky-800 dark:text-sky-300">
              <b>Ask the operator before ERP entry?</b><br />
              <span className="text-xs">Tick to make the operator enter or confirm this value when they press Submit Entry. The entry is blocked until they do. Choose below whether it blocks Submit Entry or is optional. You can tick this on as many fields as you like.</span>
            </span>
          </label>
          <label className="flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm dark:border-emerald-500/20 dark:bg-emerald-500/10">
            <input type="checkbox" className="mt-0.5" checked={targetValue} onChange={(e) => setTargetValue(e.target.checked)} />
            <span className="text-emerald-800 dark:text-emerald-300">
              <b>This is a target value?</b><br />
              <span className="text-xs">Tick to look the extracted value up in its own reference table instead of using it as-is: a match replaces it with the stored value, no match returns empty. Fill in unmatched values from the Additional Details screen (or a bulk upload) and they're remembered for next time.</span>
            </span>
          </label>
          {targetValue && (
            <label className="ml-6 flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50/60 p-3 text-sm dark:border-emerald-500/20 dark:bg-emerald-500/5">
              <input type="checkbox" className="mt-0.5" checked={fuzzyMatch} onChange={(e) => setFuzzyMatch(e.target.checked)} />
              <span className="text-emerald-800 dark:text-emerald-300">
                <b>Fuzzy match?</b><br />
                <span className="text-xs">Match on the same real-world thing despite spelling/OCR noise — "KUEHNE + NAGEL PVT. LTD." and "KUEHNE+NAGEL" count as the same match. Use this for a name (a freight forwarder, an agent); leave it off for anything where a near-miss would be wrong, like a code.</span>
              </span>
            </label>
          )}
          {askOperator && (
            <div className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 dark:border-sky-500/20 dark:bg-sky-500/10">
              <label className="block text-xs font-medium text-sky-900 dark:text-sky-200">
                Is this field mandatory or optional?
              </label>
              <select
                value={askRequired ? "mandatory" : "optional"}
                onChange={(e) => setAskRequired(e.target.value === "mandatory")}
                className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
              >
                <option value="mandatory">Mandatory — Submit Entry is blocked until it is filled in</option>
                <option value="optional">Optional — the operator may leave it blank and still submit</option>
              </select>
              <p className="mt-1 text-xs text-sky-800/80 dark:text-sky-300/80">
                {askRequired
                  ? "The job cannot be sent to the ERP until the operator supplies this value."
                  : "The operator is still asked for it, but a blank will not hold up the entry — use this for values that arrive late, such as an IGM number."}
              </p>
            </div>
          )}
          {askOperator && (
            <Input
              label="Description for the operator (optional)"
              value={askHint}
              onChange={(e) => setAskHint(e.target.value)}
              placeholder='e.g. T for Transaction, D for Deferred'
            />
          )}
          <Button onClick={saveMark} isLoading={busy} disabled={!labelName.trim()}>Save field</Button>
        </div>
      </Modal>

      {/* Cross-doc popup — choose which documents this field also appears in */}
      <Modal open={crossPopup !== null} onClose={() => setCrossPopup(null)} title={`Where else does “${crossPopup?.label}” appear?`}>
        <div className="flex flex-col gap-3">
          <p className="text-sm text-slate-500">Select every document this value also appears in — we'll find it there automatically (no need to crop again).</p>
          {group?.documents
            .filter((d) => d.id !== activeDocId)
            .map((d) => (
              <label key={d.id} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                <input
                  type="checkbox"
                  checked={crossTargets.includes(d.id)}
                  onChange={(e) =>
                    setCrossTargets((arr) => (e.target.checked ? [...arr, d.id] : arr.filter((x) => x !== d.id)))
                  }
                />
                {d.name} <span className="text-xs text-slate-400">({d.doc_type})</span>
              </label>
            ))}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={() => setCrossPopup(null)}>Skip</Button>
            <Button size="sm" onClick={confirmCrossTargets} isLoading={busy} disabled={crossTargets.length === 0}>
              Link {crossTargets.length || ""} document{crossTargets.length === 1 ? "" : "s"}
            </Button>
          </div>
        </div>
      </Modal>

      {/* Cross-field popup — a custom field has no position of its own, so the admin picks
          the exact mark(s) on other documents to compare it against, rather than a document
          (there is usually no mark sharing the field's own label to auto-match onto). */}
      <Modal
        open={crossFieldPopup !== null}
        onClose={() => setCrossFieldPopup(null)}
        title={`Which field does “${crossFieldPopup?.label}” match?`}
      >
        <div className="flex flex-col gap-3">
          <p className="text-sm text-slate-500">
            Pick the mark(s) on other documents this value should equal — e.g. "Total" on the Invoice.
          </p>
          <div className="max-h-80 overflow-y-auto flex flex-col gap-3">
            {group?.documents.map((d) =>
              !d.marks?.length ? null : (
                <div key={d.id}>
                  <p className="mb-1 text-xs font-semibold text-slate-500">{d.name}</p>
                  <div className="flex flex-col gap-1">
                    {d.marks.map((m) => (
                      <label key={m.id} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                        <input
                          type="checkbox"
                          checked={crossFieldTargets.includes(m.id)}
                          onChange={(e) =>
                            setCrossFieldTargets((arr) =>
                              e.target.checked ? [...arr, m.id] : arr.filter((x) => x !== m.id))
                          }
                        />
                        {m.label_name}
                      </label>
                    ))}
                  </div>
                </div>
              ),
            )}
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={() => setCrossFieldPopup(null)}>Skip</Button>
            <Button size="sm" onClick={confirmCrossFieldTargets} isLoading={busy} disabled={crossFieldTargets.length === 0}>
              Link {crossFieldTargets.length || ""} field{crossFieldTargets.length === 1 ? "" : "s"}
            </Button>
          </div>
        </div>
      </Modal>

      {/* Custom ruling modal */}
      <Modal open={rulingOpen} onClose={skipRuling} title="⚖️ Custom ruling — decide required documents" maxWidth="max-w-2xl">
        <div className="space-y-3">
          <p className="text-sm text-slate-500">
            Write a rule that decides, from a job's document data, <b>which documents are actually required</b>.
            The template stays trained for all documents; this rule just picks the needed subset per job.
            You can name the documents to search <b>in order</b> — each one's text is given to the AI under its
            own name. If the rule can't decide, it will ask the operator and use their answer.
          </p>
          <textarea
            value={rulingText}
            onChange={(e) => setRulingText(e.target.value)}
            rows={9}
            placeholder={`e.g. Find the incoterm, checking IN THIS ORDER and stopping at the first hit:\n  1. the BL   2. the Invoice   3. the Packing List\n\n• If FOB / FCA / EX-WORK → require BL, Invoice, Packing List, Freight Certificate.\n• If CIF / DAP → require only BL, Invoice, Packing List.\n\nIf none of those three documents contains an incoterm, ask the operator:\n"Which incoterm applies to this shipment? Choose one: FCA, EX-WORK, FOB, CIF, DAP"`}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
          />
          <p className="text-[11px] text-slate-400">Available documents in this template: {group?.documents.map((d) => d.name).join(", ")}</p>
          {error && <Alert>{error}</Alert>}
          <div className="flex justify-between">
            {group?.ruling_prompt ? (
              <Button variant="ghost" onClick={() => { setRulingText(""); }}>Clear rule</Button>
            ) : <span />}
            <div className="flex gap-2">
              <Button variant="secondary" onClick={skipRuling}>
                {rulingGate ? "Skip for now" : "Cancel"}
              </Button>
              <Button onClick={saveRuling} isLoading={rulingSaving}>
                {rulingGate ? "Save & continue" : "Save ruling"}
              </Button>
            </div>
          </div>
        </div>
      </Modal>

      {/* Custom tag modal */}
      <Modal open={customOpen} onClose={() => setCustomOpen(false)} title={cfEditId ? "Edit custom tag" : "New custom tag"} maxWidth="max-w-lg">
        <div className="space-y-4">
          <Input label="Label" value={cfLabel} onChange={(e) => setCfLabel(e.target.value)} placeholder="e.g. total_net_weight" />

          <div className="flex gap-2 text-sm">
            <button
              type="button"
              onClick={() => setCfKind("hardcoded")}
              className={`flex-1 rounded-lg border px-3 py-2 font-medium ${cfKind === "hardcoded" ? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" : "border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}
            >
              Hardcoded value
            </button>
            <button
              type="button"
              onClick={() => setCfKind("ai")}
              className={`flex-1 rounded-lg border px-3 py-2 font-medium ${cfKind === "ai" ? "border-violet-500 bg-violet-50 text-violet-700 dark:bg-violet-500/10 dark:text-violet-300" : "border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}
            >
              🤖 AI-computed
            </button>
          </div>

          {cfKind === "hardcoded" ? (
            <div>
              <Input
                label={
                  cfAskOperator
                    ? "Starting value (optional — leave blank to ask with nothing filled in)"
                    : "Fixed value (same on every job)"
                }
                value={cfValue}
                onChange={(e) => setCfValue(e.target.value)}
                placeholder={cfAskOperator ? "e.g. 011/2021, or leave blank" : "e.g. KSS ROADWAYS"}
              />
              {cfAskOperator && (
                <p className="mt-1 text-xs text-slate-500">
                  {cfValue.trim()
                    ? "The operator sees this already filled in and confirms it — good for a value that is usually the same but changes now and then, like a duty notification."
                    : "The operator gets an empty box to fill in on every job — good for a number that does not exist yet, like an IGM that has not been filed."}
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">Instruction for the AI</label>
                <textarea
                  value={cfPrompt}
                  onChange={(e) => setCfPrompt(e.target.value)}
                  rows={4}
                  placeholder={`e.g. Add the net weights from the packing list and invoice and return the total in KGS.`}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:border-violet-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                />
              </div>
              <div>
                <p className="mb-1 text-xs font-medium text-slate-600 dark:text-slate-300">Which documents should the AI use?</p>
                <p className="mb-1.5 text-[11px] text-slate-400">Tick the documents whose content decides this field. (None ticked = all documents.)</p>
                <div className="flex flex-col gap-1 rounded-lg border border-slate-200 p-2 dark:border-slate-700">
                  {group?.documents.map((d) => (
                    <label key={d.id} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
                      <input
                        type="checkbox"
                        checked={cfDocs.includes(d.id)}
                        onChange={(e) => setCfDocs((arr) => (e.target.checked ? [...arr, d.id] : arr.filter((x) => x !== d.id)))}
                      />
                      {d.name} <span className="text-xs text-slate-400">({d.doc_type})</span>
                    </label>
                  ))}
                </div>
              </div>
              <label className="flex items-start gap-2 rounded-lg border border-violet-200 bg-violet-50 p-3 text-sm dark:border-violet-500/20 dark:bg-violet-500/10">
                <input type="checkbox" className="mt-0.5" checked={cfMultiValue} onChange={(e) => setCfMultiValue(e.target.checked)} />
                <span className="text-violet-800 dark:text-violet-300">
                  <b>Multiple values from document?</b><br />
                  <span className="text-xs">
                    Tick when the instruction describes a single item that repeats — every container
                    number, each line's HS code. The AI reads the ticked document(s) itself and returns
                    every instance it finds — value 1, value 2, value 3 and so on — instead of one value
                    for the whole field. Same idea as a Mark ticked "multiple values in this document",
                    just for an AI-computed tag instead of a cropped box.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm dark:border-amber-500/20 dark:bg-amber-500/10">
                <input type="checkbox" className="mt-0.5" checked={cfVerify} onChange={(e) => setCfVerify(e.target.checked)} />
                <span className="text-amber-800 dark:text-amber-300">
                  <b>Present in another document?</b><br />
                  <span className="text-xs">Tick to cross-verify this value against your other documents (you'll pick which mark — no re-cropping needed).</span>
                </span>
              </label>
              <label className="flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm dark:border-emerald-500/20 dark:bg-emerald-500/10">
                <input type="checkbox" className="mt-0.5" checked={cfTargetValue} onChange={(e) => setCfTargetValue(e.target.checked)} />
                <span className="text-emerald-800 dark:text-emerald-300">
                  <b>This is a target value?</b><br />
                  <span className="text-xs">Tick to look the computed value up in its own reference table instead of using it as-is: a match replaces it with the stored value, no match returns empty. Fill in unmatched values from the Additional Details screen (or a bulk upload) and they're remembered for next time.</span>
                </span>
              </label>
              {cfTargetValue && (
                <label className="ml-6 flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50/60 p-3 text-sm dark:border-emerald-500/20 dark:bg-emerald-500/5">
                  <input type="checkbox" className="mt-0.5" checked={cfFuzzyMatch} onChange={(e) => setCfFuzzyMatch(e.target.checked)} />
                  <span className="text-emerald-800 dark:text-emerald-300">
                    <b>Fuzzy match?</b><br />
                    <span className="text-xs">Match on the same real-world thing despite spelling/OCR noise — "KUEHNE + NAGEL PVT. LTD." and "KUEHNE+NAGEL" count as the same match. Use this for a name (a freight forwarder, an agent); leave it off for anything where a near-miss would be wrong, like a code.</span>
                  </span>
                </label>
              )}
            </div>
          )}

          <label className="flex items-start gap-2 rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm dark:border-sky-500/20 dark:bg-sky-500/10">
            <input type="checkbox" className="mt-0.5" checked={cfAskOperator} onChange={(e) => setCfAskOperator(e.target.checked)} />
            <span className="text-sky-800 dark:text-sky-300">
              <b>Ask the operator before ERP entry?</b><br />
              <span className="text-xs">Tick for values the operator must supply or confirm each job — e.g. the insurance percentage. Any value above becomes the default they confirm. Choose below whether it blocks Submit Entry or is optional.</span>
            </span>
          </label>
          {cfAskOperator && (
            <div className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 dark:border-sky-500/20 dark:bg-sky-500/10">
              <label className="block text-xs font-medium text-sky-900 dark:text-sky-200">
                Is this field mandatory or optional?
              </label>
              <select
                value={cfAskRequired ? "mandatory" : "optional"}
                onChange={(e) => setCfAskRequired(e.target.value === "mandatory")}
                className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
              >
                <option value="mandatory">Mandatory — Submit Entry is blocked until it is filled in</option>
                <option value="optional">Optional — the operator may leave it blank and still submit</option>
              </select>
              <p className="mt-1 text-xs text-sky-800/80 dark:text-sky-300/80">
                {cfAskRequired
                  ? "The job cannot be sent to the ERP until the operator supplies this value."
                  : "The operator is still asked for it, but a blank will not hold up the entry — use this for values that arrive late, such as an IGM number."}
              </p>
            </div>
          )}
          {cfAskOperator && (
            <label className="flex items-start gap-2 rounded-lg border border-violet-200 bg-violet-50 p-3 text-sm dark:border-violet-500/20 dark:bg-violet-500/10">
              <input type="checkbox" className="mt-0.5" checked={cfPerRow} onChange={(e) => setCfPerRow(e.target.checked)} />
              <span className="text-violet-800 dark:text-violet-300">
                <b>Ask once per product line?</b><br />
                <span className="text-xs">
                  Tick for a value that differs per line item — the CTH / HS code on each product.
                  The operator gets one numbered box per invoice line instead of a single box, and the
                  ERP row loop feeds line 3's value into line 3 of the product grid. Leave unticked for a
                  value that is the same for the whole job.
                </span>
              </span>
            </label>
          )}
          {cfAskOperator && (
            <Input
              label="Description for the operator (optional)"
              value={cfAskHint}
              onChange={(e) => setCfAskHint(e.target.value)}
              placeholder='e.g. T for Transaction, D for Deferred'
            />
          )}
          {error && <Alert>{error}</Alert>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setCustomOpen(false)}>Cancel</Button>
            <Button onClick={saveCustomField} isLoading={cfSaving}>{cfEditId ? "Save changes" : "Add tag"}</Button>
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
