import { apiClient } from "./client";
import type {
  CrossDocLink,
  DemoResult,
  DocumentDeclaration,
  Mark,
  ReferenceSheet,
  TemplateDocument,
  TemplateGroup,
  TemplateGroupDetail,
} from "../types/onboarding";

export type { CustomField, ReferenceSheet } from "../types/onboarding";

// --- Step 1: groups + declared documents ---
export async function createGroup(payload: {
  tenant_id: string;
  name: string;
  /** Which transport mode this template is for — required. */
  mode: string;
  documents: DocumentDeclaration[];
}): Promise<TemplateGroupDetail> {
  const { data } = await apiClient.post<TemplateGroupDetail>("/template-groups", payload);
  return data;
}

export async function listGroups(tenantId?: string): Promise<TemplateGroup[]> {
  const { data } = await apiClient.get<TemplateGroup[]>("/template-groups", {
    params: tenantId ? { tenant_id: tenantId } : undefined,
  });
  return data;
}

/** Copy a whole template set - documents, sample files, marks, custom fields, links.
 *  The email routing is deliberately NOT copied, so the copy cannot take the original's mail. */
export async function duplicateGroup(
  groupId: string,
  name: string,
): Promise<TemplateGroupDetail> {
  const { data } = await apiClient.post<TemplateGroupDetail>(
    `/template-groups/${groupId}/duplicate`,
    { name },
  );
  return data;
}

export async function getGroup(groupId: string): Promise<TemplateGroupDetail> {
  const { data } = await apiClient.get<TemplateGroupDetail>(`/template-groups/${groupId}`);
  return data;
}

export async function deleteGroup(groupId: string): Promise<void> {
  await apiClient.delete(`/template-groups/${groupId}`);
}

export async function createCustomField(
  groupId: string,
  payload: {
    label_name: string;
    kind: "hardcoded" | "ai" | "composite";
    hardcoded_value?: string | null;
    ai_prompt?: string | null;
    source_document_ids?: string[];
    ask_operator?: boolean;
    ask_operator_required?: boolean;
    per_row?: boolean;
    ask_operator_hint?: string | null;
    multi_value_from_document?: boolean;
    is_target_value?: boolean;
    fuzzy_match?: boolean;
    example_value?: string | null;
    paired_custom_field_id?: string | null;
    picker_heading?: string | null;
    sync_field_ids?: string[] | null;
    composite_source_labels?: string[] | null;
  },
): Promise<import("../types/onboarding").CustomField> {
  const { data } = await apiClient.post(`/template-groups/${groupId}/custom-fields`, payload);
  return data;
}

export async function deleteCustomField(fieldId: string): Promise<void> {
  await apiClient.delete(`/custom-fields/${fieldId}`);
}

// --- Target-value reference tables (Settings page) ---
// The same five operations exist twice on the backend - once under /custom-fields/{id}/...,
// once under /marks/{id}/... - because a target-value field can be either kind. `ownerType`
// here just picks which URL prefix to call; the shape of everything else is identical.
export type ReferenceOwnerType = "custom-fields" | "marks";

export interface ReferenceValueRow {
  s_no: number;
  match_value_1: string;
  match_value_2: string;
  match_value_3: string;
  match_value_4: string;
  resolved_value: string;
  updated_at?: string | null;
}

export async function listReferenceValues(ownerType: ReferenceOwnerType, ownerId: string): Promise<ReferenceValueRow[]> {
  const { data } = await apiClient.get<{ values: ReferenceValueRow[] }>(`/${ownerType}/${ownerId}/reference-values`);
  return data.values;
}

export async function downloadReferenceValuesTemplate(ownerType: ReferenceOwnerType, ownerId: string): Promise<{ url: string; filename: string }> {
  const { data, headers } = await apiClient.get<Blob>(`/${ownerType}/${ownerId}/reference-values/template`, {
    responseType: "blob",
  });
  const disposition = String(headers["content-disposition"] ?? "");
  const match = /filename="?([^"]+)"?/.exec(disposition);
  return { url: URL.createObjectURL(data), filename: match?.[1] ?? "reference_template.xlsx" };
}

export interface ReferenceUploadConflict {
  s_no: number;
  match_value_1: string;
  match_value_2: string;
  match_value_3: string;
  match_value_4: string;
  existing_value: string;
  uploaded_value: string;
}

export interface ReferenceUploadResult {
  ok: boolean;
  rows_added: number;
  rows_unchanged: number;
  rows_skipped: number;
  conflicts: ReferenceUploadConflict[];
}

export async function uploadReferenceValues(
  ownerType: ReferenceOwnerType, ownerId: string, file: File,
): Promise<ReferenceUploadResult> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<ReferenceUploadResult>(
    `/${ownerType}/${ownerId}/reference-values/upload`, form,
  );
  return data;
}

/** Resolve one conflict row: apply the uploaded value over the existing one. Leaving the
 *  existing value in place needs no call at all - that IS "keep the old value". */
export async function updateReferenceValue(
  ownerType: ReferenceOwnerType, ownerId: string, valueId: number, resolvedValue: string,
): Promise<ReferenceValueRow> {
  const { data } = await apiClient.patch<ReferenceValueRow>(
    `/${ownerType}/${ownerId}/reference-values/${valueId}`, { resolved_value: resolvedValue },
  );
  return data;
}

export async function deleteReferenceValue(ownerType: ReferenceOwnerType, ownerId: string, valueId: number): Promise<void> {
  await apiClient.delete(`/${ownerType}/${ownerId}/reference-values/${valueId}`);
}

export async function setRuling(groupId: string, rulingPrompt: string | null): Promise<TemplateGroupDetail> {
  const { data } = await apiClient.patch<TemplateGroupDetail>(`/template-groups/${groupId}/ruling`, {
    ruling_prompt: rulingPrompt,
  });
  return data;
}

export async function finalizeGroup(groupId: string): Promise<TemplateGroup> {
  const { data } = await apiClient.post<TemplateGroup>(`/template-groups/${groupId}/finalize`);
  return data;
}

export async function setPullEmail(
  groupId: string,
  email: string | null,
  operatorId?: string | null,
): Promise<TemplateGroup> {
  const body: Record<string, unknown> = { pull_email: email };
  if (operatorId !== undefined) body.pull_operator_id = operatorId; // null clears; omit = leave as-is
  const { data } = await apiClient.patch<TemplateGroup>(`/template-groups/${groupId}/pull-email`, body);
  return data;
}

/** Change which transport mode a template is tagged for. */
export async function setGroupMode(groupId: string, mode: string): Promise<TemplateGroup> {
  const { data } = await apiClient.patch<TemplateGroup>(`/template-groups/${groupId}/mode`, { mode });
  return data;
}

export async function renameGroup(groupId: string, name: string): Promise<TemplateGroup> {
  const { data } = await apiClient.patch<TemplateGroup>(`/template-groups/${groupId}/name`, { name });
  return data;
}

// --- Step 2: upload a document file + page previews ---
export async function uploadDocument(documentId: string, file: File): Promise<TemplateDocument> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<TemplateDocument>(
    `/template-documents/${documentId}/upload`,
    form,
  );
  return data;
}

/** Mandatory (the default) or optional — whether a job can pass Document Capture without
 *  this document ever being uploaded. */
export async function setDocumentRequired(documentId: string, isRequired: boolean): Promise<TemplateDocument> {
  const { data } = await apiClient.patch<TemplateDocument>(
    `/template-documents/${documentId}/required`,
    { is_required: isRequired },
  );
  return data;
}

/** Page previews require the auth cookie, which a cross-site <img> won't send.
 * Fetch as an authenticated blob and hand back an object URL (revoke when done). */
export async function fetchPageObjectUrl(documentId: string, page: number): Promise<string> {
  const { data } = await apiClient.get<Blob>(
    `/template-documents/${documentId}/pages/${page}`,
    { responseType: "blob" },
  );
  return URL.createObjectURL(data);
}

// --- Step 3: marks (bounding boxes) ---
export async function createMark(
  documentId: string,
  payload: {
    label_name: string;
    page_number: number;
    x: number;
    y: number;
    width: number;
    height: number;
    color: string;
    verify_with_other_document?: boolean;
    ask_operator?: boolean;
    ask_operator_required?: boolean;
    per_row?: boolean;
    ask_operator_hint?: string | null;
    /** Document repeats this field — extraction returns one value per table row. */
    is_multi_value?: boolean;
    /** Look the extracted value up in its own reference table; a match replaces it, no
     *  match returns empty. */
    is_target_value?: boolean;
    /** is_target_value only: match on the same real-world thing despite formatting noise. */
    fuzzy_match?: boolean;
  },
): Promise<Mark> {
  const { data } = await apiClient.post<Mark>(`/template-documents/${documentId}/marks`, payload);
  return data;
}

export async function deleteMark(markId: string): Promise<void> {
  await apiClient.delete(`/marks/${markId}`);
}

export async function correctMark(markId: string, correctionPrompt: string): Promise<Mark> {
  const { data } = await apiClient.patch<Mark>(`/marks/${markId}/correct`, {
    correction_prompt: correctionPrompt,
  });
  return data;
}

export async function editMark(
  markId: string,
  patch: Partial<{
    label_name: string;
    extraction_prompt: string;
    anchor_variations: string[];
    semantic_description: string;
    value_format_hint: string;
  }>,
): Promise<Mark> {
  const { data } = await apiClient.patch<Mark>(`/marks/${markId}`, patch);
  return data;
}

// --- Step 3 cross-doc links ---
export async function createCrossDocLink(payload: {
  source_mark_id: string;
  target_mark_id: string;
  condition?: string;
}): Promise<CrossDocLink> {
  const { data } = await apiClient.post<CrossDocLink>("/cross-doc-links", payload);
  return data;
}

export async function deleteCrossDocLink(linkId: string): Promise<void> {
  await apiClient.delete(`/cross-doc-links/${linkId}`);
}

/** Cross-verify a custom field (an AI-computed value with no position on any document)
 * against mark(s) picked directly on other documents - "Total Amount (Calculated)" against
 * the Invoice's own "Total" mark, say. Unlike linkFieldToDocuments (which picks DOCUMENTS
 * and matches by the field's own label), the admin picks the mark(s) themselves - a custom
 * field's label rarely matches any mark's. */
export async function linkCustomFieldToMarks(
  customFieldId: string,
  payload: { target_mark_ids: string[]; condition?: string },
): Promise<CrossDocLink[]> {
  const { data } = await apiClient.post<CrossDocLink[]>(
    `/custom-fields/${customFieldId}/link-marks`,
    payload,
  );
  return data;
}

export async function unlinkCustomFieldFromMark(customFieldId: string, linkId: string): Promise<void> {
  await apiClient.delete(`/custom-fields/${customFieldId}/link-marks/${linkId}`);
}

/** Cross-doc verify without re-cropping: link a source field to other documents;
 * the backend copies its profile so the value is found on each doc by meaning. */
export async function linkFieldToDocuments(
  sourceMarkId: string,
  payload: { target_document_ids: string[]; condition?: string },
): Promise<CrossDocLink[]> {
  const { data } = await apiClient.post<CrossDocLink[]>(
    `/field-marks/${sourceMarkId}/link-documents`,
    payload,
  );
  return data;
}

// --- Step 5 demo ---
export async function demoExtract(documentId: string): Promise<DemoResult> {
  const { data } = await apiClient.get<DemoResult>(`/template-documents/${documentId}/demo`);
  return data;
}

/** Run the configured prompts against an UNSEEN uploaded file (no persistence). */
export async function testExtract(documentId: string, file: File): Promise<DemoResult> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<DemoResult>(
    `/template-documents/${documentId}/test-extract`,
    form,
  );
  return data;
}


/** Edit an existing custom tag in place. Only the keys you send are changed.
 *  Never delete-and-recreate to rename: job values cascade-delete with the tag. */
export async function updateCustomField(
  fieldId: string,
  patch: Partial<{
    label_name: string;
    kind: string;
    hardcoded_value: string | null;
    ai_prompt: string | null;
    source_document_ids: string[];
    ask_operator: boolean;
    ask_operator_required: boolean;
    per_row: boolean;
    ask_operator_hint: string | null;
    lookup_key_label: string | null;
    lookup_match_columns: string[] | null;
    lookup_return_column: string | null;
    multi_value_from_document: boolean;
    verify_with_other_document: boolean;
    is_target_value: boolean;
    fuzzy_match: boolean;
    paired_custom_field_id: string | null;
    picker_heading: string | null;
    sync_field_ids: string[] | null;
    composite_source_labels: string[] | null;
  }>,
) {
  const { data } = await apiClient.patch(`/custom-fields/${fieldId}`, patch);
  return data;
}


// --------------------------------------------------------------------------- //
// Entry mode — how this customer's data reaches the ERP
// --------------------------------------------------------------------------- //
/** One column of the import sheet. `column` is the letter in the customer's own workbook, kept
 *  so their layout (including spacer columns) is reproduced exactly - the ERP matches on it. */
export interface ExcelColumn {
  column?: string;
  header: string;
  field: string;
  /** A constant this column always carries, where no data field feeds it ('KGS', '0.00'). */
  value?: string;
  /** One value per row, for a fixed block. May be [[Field Name]] to read a field instead. */
  values?: string[];
  /** Cut the value to this many characters - some ICEGATE columns are shorter than the text
   *  that feeds them, and an over-length field has the import rejected. */
  max_len?: number;
}

/** One sheet of the customer's workbook, with its own heading row and row count.
 *  scope="job" writes exactly one row (GENERAL, SHIPMENT); scope="line" writes one row per
 *  line item (ITEMS). A sheet must not repeat just because one of its columns happens to be
 *  fed by a per-line field. */
export interface ExcelSheetConfig {
  sheet: string;
  header_row: number;
  /** fixed = a set block of rows spelled out in the mapping (STATEMENT's declaration codes).
   *  invoice = one row per invoice: a shipment covered by three invoices is ONE customs
   *  entry whose INVOICES sheet carries three rows. */
  scope: "job" | "line" | "fixed" | "invoice";
  columns: ExcelColumn[];
}

export interface ExcelConfig {
  source: "blank" | "template";
  /** The single-sheet shape. Kept for configurations saved before multi-sheet. */
  sheet?: string;
  header_row?: number;
  file_name?: string;
  columns: ExcelColumn[];
  /** Several sheets to fill — what a real ERP import workbook needs. */
  sheets?: ExcelSheetConfig[];
}

export interface ExcelTemplateInfo {
  sheets: string[];
  sheet: string | null;
  header_row: number;
  headers: { column: string; header: string }[];
  file_name?: string;
}

/** Upload the customer's own import workbook and read back the columns it expects. */
export async function uploadExcelTemplate(
  groupId: string,
  file: File,
  sheet?: string,
  headerRow = 1,
): Promise<ExcelTemplateInfo> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<ExcelTemplateInfo>(
    `/template-groups/${groupId}/excel-template`,
    form,
    { params: { sheet, header_row: headerRow }, headers: { "Content-Type": "multipart/form-data" } },
  );
  return data;
}

/** Attach the customer's own reference sheet (material code -> CTH, and whatever else it holds). */
export async function uploadReferenceSheet(
  groupId: string,
  file: File,
): Promise<ReferenceSheet> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<ReferenceSheet>(
    `/template-groups/${groupId}/material-master`,
    form,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return data;
}

export async function getReferenceSheet(groupId: string): Promise<ReferenceSheet> {
  const { data } = await apiClient.get<ReferenceSheet>(
    `/template-groups/${groupId}/material-master`,
  );
  return data;
}

/** The sheet's own column headings, so a field is pointed at them by name. */
export async function getReferenceColumns(groupId: string): Promise<string[]> {
  const { data } = await apiClient.get<{ columns: string[] }>(
    `/template-groups/${groupId}/material-master/columns`,
  );
  return data.columns ?? [];
}

export async function deleteReferenceSheet(groupId: string): Promise<void> {
  await apiClient.delete(`/template-groups/${groupId}/material-master`);
}

export async function setEntryMode(
  groupId: string,
  entryMode: "fields" | "excel",
  excelConfig?: ExcelConfig | null,
): Promise<TemplateGroupDetail> {
  const { data } = await apiClient.patch<TemplateGroupDetail>(
    `/template-groups/${groupId}/entry-mode`,
    { entry_mode: entryMode, excel_config: excelConfig ?? null },
  );
  return data;
}

/** Re-read the workbook ALREADY uploaded for this group, for a different sheet or header row.
 *  Separate from the upload so picking a sheet does not mean choosing the file again. */
export async function readExcelTemplateHeaders(
  groupId: string,
  sheet?: string | null,
  headerRow = 1,
): Promise<ExcelTemplateInfo & { warning?: string }> {
  const { data } = await apiClient.post<ExcelTemplateInfo & { warning?: string }>(
    `/template-groups/${groupId}/excel-template/headers`,
    { sheet: sheet ?? null, header_row: headerRow },
  );
  return data;
}
