import { apiClient } from "./client";
import type { CompositePiece, CustomField, Mark, TemplateDocument, TemplateGroupDetail } from "../types/onboarding";

// Customer Onboarding -> the LIVE template (backend app/api/v1/onboarding_template.py).
// Every live-field edit goes through the same routes the old Document Templates screens use.

/** A live box (FieldMark) with every setting the backend holds. */
export type LiveMark = Mark & {
  fixed_options?: string[] | null;
  dropdown_style?: "plain" | "search" | "search_free" | null;
};

/** A live custom field with every setting the backend holds. */
export type LiveCustomField = CustomField & {
  skip_material_master?: boolean;
  lookup_ai_fallback?: boolean;
  hidden_from_capture?: boolean;
  requires_value_of?: string | null;
  composite_separators?: string[] | null;
};

export type LiveDocument = TemplateDocument & { manual_entry_when_missing?: boolean; marks: LiveMark[] };

export type LiveGroup = Omit<TemplateGroupDetail, "documents" | "custom_fields"> & {
  documents: LiveDocument[];
  custom_fields?: LiveCustomField[];
  pull_email?: string | null;
  pull_operator_id?: string | null;
  prealert_shipments?: boolean;
  importer_requires_gstin?: boolean;
};

/** "m:<FieldMark id>" or "c:<CustomField id>" - the same refs Screen Designer uses. */
export type FieldRef = string;

export async function getLiveGroup(groupId: string): Promise<LiveGroup> {
  const { data } = await apiClient.get<LiveGroup>(`/template-groups/${groupId}`);
  return data;
}

export async function patchMark(markId: string, patch: Record<string, unknown>): Promise<LiveMark> {
  const { data } = await apiClient.patch<LiveMark>(`/marks/${markId}`, patch);
  return data;
}

export async function patchCustomField(fieldId: string, patch: Record<string, unknown>): Promise<LiveCustomField> {
  const { data } = await apiClient.patch<LiveCustomField>(`/custom-fields/${fieldId}`, patch);
  return data;
}

export async function createLiveCustomField(groupId: string, payload: Record<string, unknown>): Promise<LiveCustomField> {
  const { data } = await apiClient.post<LiveCustomField>(`/template-groups/${groupId}/custom-fields`, payload);
  return data;
}

// --- links ---------------------------------------------------------------------------------------

export interface FieldLink {
  erp_field_id: string;
  sheet: string;
  column: string | null;
  excel_name: string;
  display_label: string | null;
  category: string | null;
  source_document: string | null;
  template_document_id: string | null;
  /** The live field's label_name, when linked. */
  label: string | null;
  ref: FieldRef | null;
  ref_document_id: string | null;
  how: "excel" | "erp_ui" | null;
  /** Not linked, but a live field has a matching name. */
  suggested_label: string | null;
  in_mapping: boolean;
  column_rule: ExcelColumnRule | null;
}

export interface SpecialName {
  name: string;
  present: boolean;
}

export async function getLinks(groupId: string): Promise<{ workbook_id: string | null; fields: FieldLink[]; special_names: SpecialName[] }> {
  const { data } = await apiClient.get(`/onboarding/template/groups/${groupId}/links`);
  return data;
}

export async function linkToLiveField(erpFieldId: string, label: string): Promise<void> {
  await apiClient.put(`/onboarding/template/erp-fields/${erpFieldId}/link`, { label });
}

export async function putTemplateBox(
  erpFieldId: string,
  box: { page: number; x: number; y: number; width: number; height: number; label_name?: string | null; line_item?: boolean },
): Promise<{ ref: FieldRef; mark_id: string; template_document_id: string; label: string; created: boolean; warning: string | null }> {
  const { data } = await apiClient.post(`/onboarding/template/erp-fields/${erpFieldId}/template-box`, box);
  return data;
}

export async function createTemplateField(
  erpFieldId: string,
  body: { kind?: string; label_name?: string | null } = {},
): Promise<{ ref: FieldRef; custom_field_id: string; label: string; created: boolean }> {
  const { data } = await apiClient.post(`/onboarding/template/erp-fields/${erpFieldId}/template-field`, body);
  return data;
}

// --- ERP Excel mapping (Customer Template Mapping) -------------------------------------------------

export interface PickBy {
  field: string;
  options: Record<string, string>;
  default?: string;
}

export interface ExcelColumnRule {
  field?: string;
  value?: string;
  values?: string[];
  pick_by?: PickBy;
  from_document?: string;
  fallback_fields?: string[];
  transform?: string;
  max_len?: number;
}

export interface ExcelSheetPlan {
  sheet: string;
  header_row: number;
  scope: "job" | "line" | "invoice" | "fixed" | string;
  columns: ({ column?: string; header?: string } & ExcelColumnRule)[];
}

export async function getExcelMapping(groupId: string): Promise<{
  entry_mode: string; file_name: string | null; sheets: ExcelSheetPlan[]; transforms: string[]; scopes: string[]; documents: string[];
}> {
  const { data } = await apiClient.get(`/onboarding/template/groups/${groupId}/excel`);
  return data;
}

/** Only the keys in `rule` change; null / "" / [] removes that key. */
export async function setColumnRule(
  groupId: string,
  body: { sheet: string; column?: string | null; header?: string | null; rule: Record<string, unknown> },
): Promise<void> {
  await apiClient.patch(`/onboarding/template/groups/${groupId}/excel-column`, body);
}

export async function setSheetPlan(
  groupId: string,
  body: { sheet: string; scope?: string; header_row?: number; remove?: boolean },
): Promise<void> {
  await apiClient.patch(`/onboarding/template/groups/${groupId}/excel-sheet`, body);
}

// --- template-level settings ---------------------------------------------------------------------

export async function addTemplateDocument(
  groupId: string, body: { name: string; doc_type?: string; is_required?: boolean },
): Promise<{ id: string; name: string }> {
  const { data } = await apiClient.post(`/onboarding/template/groups/${groupId}/documents`, body);
  return data;
}

/** Refused (409) while any job uses the document. */
export async function deleteTemplateDocument(documentId: string): Promise<void> {
  await apiClient.delete(`/onboarding/template/documents/${documentId}`);
}

export async function setDocumentOrder(documentId: string, orderIndex: number): Promise<void> {
  await apiClient.patch(`/onboarding/template/documents/${documentId}/order`, { order_index: orderIndex });
}

export async function setDocumentManualEntry(documentId: string, value: boolean): Promise<void> {
  await apiClient.patch(`/template-documents/${documentId}/manual-entry`, { manual_entry_when_missing: value });
}

export async function renameDocument(documentId: string, name: string): Promise<void> {
  await apiClient.patch(`/template-documents/${documentId}/name`, { name });
}

export async function setDocumentType(documentId: string, docType: string): Promise<void> {
  await apiClient.patch(`/template-documents/${documentId}/doc-type`, { doc_type: docType });
}

export async function setPrealertShipments(groupId: string, value: boolean): Promise<void> {
  await apiClient.patch(`/template-groups/${groupId}/prealert-shipments`, { prealert_shipments: value });
}

export async function setImporterRequiresGstin(groupId: string, value: boolean): Promise<void> {
  await apiClient.patch(`/template-groups/${groupId}/importer-gstin`, { importer_requires_gstin: value });
}

export interface PromptHistoryRow {
  id: string;
  label_name: string;
  action: string;
  old_prompt: string | null;
  new_prompt: string | null;
  old_format_hint: string | null;
  new_format_hint: string | null;
  changed_by: string | null;
  created_at: string | null;
}

export async function getPromptHistory(groupId: string): Promise<PromptHistoryRow[]> {
  const { data } = await apiClient.get(`/onboarding/template/groups/${groupId}/prompt-history`);
  return data;
}

export interface TemplateUser {
  id: string;
  name: string;
  email: string;
  role: string;
  assigned: boolean;
  assignment_count: number;
  /** No assignments at all = the user sees every template of the customer. */
  sees_all: boolean;
}

export async function getTemplateUsers(groupId: string): Promise<TemplateUser[]> {
  const { data } = await apiClient.get(`/onboarding/template/groups/${groupId}/users`);
  return data;
}

export async function setTemplateUser(groupId: string, userId: string, assigned: boolean): Promise<void> {
  await apiClient.put(`/onboarding/template/groups/${groupId}/users/${userId}`, { assigned });
}

export interface ClassifierSample {
  id: string;
  template_document_id: string;
  document: string | null;
  excerpt: string;
  from_job_document: string | null;
  created_at: string | null;
}

export async function getClassifierSamples(groupId: string): Promise<ClassifierSample[]> {
  const { data } = await apiClient.get(`/onboarding/template/groups/${groupId}/document-samples`);
  return data;
}

export async function deleteClassifierSample(sampleId: string): Promise<void> {
  await apiClient.delete(`/onboarding/template/document-samples/${sampleId}`);
}

export type { CompositePiece };

/** Best error text from an API failure. */
export function apiError(e: unknown, fallback: string): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg);
  return fallback;
}

/** Step 4: run an AI-Derived field's rule over the template's sample documents (nothing saved). */
export async function testAiField(customFieldId: string): Promise<{
  field: string; results: { document: string; value: string | null; note: string | null }[];
}> {
  const { data } = await apiClient.post(`/onboarding/template/custom-fields/${customFieldId}/test-ai`);
  return data;
}

/** Step 3: build the reading prompts (label variations, meaning, format, prompt) from each box.
 *  only_missing (default) leaves a box that already has a prompt alone. */
export async function generatePrompts(groupId: string, body: { mark_ids?: string[]; only_missing?: boolean }): Promise<{
  generated: number; skipped: number; mark_ids: string[];
}> {
  const { data } = await apiClient.post(`/onboarding/template/groups/${groupId}/generate-prompts`, body);
  return data;
}

/** Step 6: the end-to-end test - samples read, ERP Excel built, compared with the customer's. */
export interface E2eColumn {
  column: string | null; header: string | null; field: string; read_from: string | null;
  expected: string; built: string; status: "match" | "differs" | "missing" | "on_job" | "extra";
}
export interface E2eResult {
  summary: { match: number; differs: number; missing: number; on_job: number; extra: number; compared: number; match_rate: number | null };
  sheets: { sheet: string; scope: string; rows_built: number; columns: E2eColumn[] }[];
  unread_documents: string[];
}
export async function endToEndTest(groupId: string): Promise<E2eResult> {
  const { data } = await apiClient.post<E2eResult>(`/onboarding/template/groups/${groupId}/end-to-end-test`);
  return data;
}
