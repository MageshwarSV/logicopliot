import { apiClient } from "./client";

// Customer Onboarding - Step 1: ERP Field Classification.

// The value source of an ERP field - one bucket each in Step 1.
export type ErpCategory = "document" | "reference" | "fixed" | "operator" | "ai" | "formula";
export const ERP_DATA_TYPES = ["text", "number", "decimal", "date", "boolean"] as const;

export interface ErpSummary {
  total: number;
  classified: number;
  unclassified: number;
  percent: number;
}

export interface ErpCustomer {
  group_id: string;
  name: string;
  tenant: string;
  mode: string | null;
  workbook: ({ id: string; file_name: string; status: string; sheet_count: number } & ErpSummary) | null;
  /** The template already runs on Excel entry - Step 0 can import its current set-up. */
  excel_setup?: boolean;
}

export interface ErpField {
  id: string;
  sheet_name: string;
  column_key: string | null;
  column_index: number | null;
  excel_name: string;
  is_custom: boolean;
  category: ErpCategory | null;
  position: number;
  display_label: string | null;
  data_type: string;
  description: string | null;
  mandatory: boolean;
  default_value: string | null;
  validation: string | null;
  formula: string | null;
  /** AI-derived fields: the instruction the AI follows. */
  ai_prompt: string | null;
  erp_master_ref: string | null;
  contact_ref: string | null;
  /** Document fields: which of the workbook's documents it is read from (null = not assigned). */
  source_document: string | null;
  /** Step 4: the category's own configuration - see FixedConfig / OperatorConfig. */
  config: Record<string, unknown> | null;
  samples: string[];
}

export interface FixedConfig {
  value: string;
  per_importer: { importer: string; value: string }[];
  hidden: boolean;
}

export interface OperatorConfig {
  input_type: "text" | "number" | "date" | "dropdown";
  choices: string[];
  default: string;
}

export interface ErpDocument {
  id: string;
  name: string;
  template_document_id: string | null;
  /** Additional Details: always there, cannot be renamed or removed. */
  fixed?: boolean;
}

export interface ErpWorkbook {
  id: string;
  group_id: string;
  customer: string;
  file_name: string;
  status: "draft" | "complete" | string;
  documents: ErpDocument[];
  sheets: { name: string; header_row: number; column_count: number }[];
  fields: ErpField[];
  summary: ErpSummary;
  updated_at: string | null;
  updated_by: string | null;
}

export type ErpFieldPatch = Partial<Pick<ErpField,
  "category" | "position" | "display_label" | "data_type" | "description" | "mandatory" |
  "default_value" | "validation" | "formula" | "ai_prompt" | "erp_master_ref" | "contact_ref" | "source_document" | "config">>;

export async function listCustomers(): Promise<ErpCustomer[]> {
  const { data } = await apiClient.get<ErpCustomer[]>("/onboarding/erp/customers");
  return data;
}

export async function getWorkbook(groupId: string): Promise<ErpWorkbook | null> {
  const { data } = await apiClient.get<ErpWorkbook | null>(`/onboarding/erp/groups/${groupId}/workbook`);
  return data;
}

export async function uploadWorkbook(groupId: string, file: File): Promise<ErpWorkbook> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<ErpWorkbook>(`/onboarding/erp/groups/${groupId}/workbook`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function setHeaderRow(workbookId: string, sheet: string, headerRow: number): Promise<ErpWorkbook> {
  const { data } = await apiClient.post<ErpWorkbook>(`/onboarding/erp/workbooks/${workbookId}/header-row`, {
    sheet, header_row: headerRow,
  });
  return data;
}

export async function updateField(fieldId: string, patch: ErpFieldPatch): Promise<{ field: ErpField; summary: ErpSummary; status: string }> {
  const { data } = await apiClient.patch(`/onboarding/erp/fields/${fieldId}`, patch);
  return data;
}

export async function addCustomField(workbookId: string, sheet: string, name: string, category: ErpCategory,
  sourceDocument: string | null = null): Promise<ErpField> {
  const { data } = await apiClient.post<{ field: ErpField }>(`/onboarding/erp/workbooks/${workbookId}/fields`, {
    sheet, name, category, source_document: sourceDocument,
  });
  return data.field;
}

export async function addDocument(workbookId: string, name: string): Promise<ErpWorkbook> {
  const { data } = await apiClient.post<ErpWorkbook>(`/onboarding/erp/workbooks/${workbookId}/documents`, { name });
  return data;
}

export async function renameDocument(workbookId: string, docId: string, name: string): Promise<ErpWorkbook> {
  const { data } = await apiClient.patch<ErpWorkbook>(`/onboarding/erp/workbooks/${workbookId}/documents/${docId}`, { name });
  return data;
}

export async function removeDocument(workbookId: string, docId: string): Promise<ErpWorkbook> {
  const { data } = await apiClient.delete<ErpWorkbook>(`/onboarding/erp/workbooks/${workbookId}/documents/${docId}`);
  return data;
}

export async function deleteCustomField(fieldId: string): Promise<void> {
  await apiClient.delete(`/onboarding/erp/fields/${fieldId}`);
}

export async function setStatus(workbookId: string, status: "draft" | "complete"): Promise<ErpWorkbook> {
  const { data } = await apiClient.post<ErpWorkbook>(`/onboarding/erp/workbooks/${workbookId}/status`, { status });
  return data;
}

// ---- Step 2: Document Mapping ----------------------------------------------------------------

export interface DocMark {
  field_id: string;
  doc_id: string;
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
  line_item: boolean;
  detected_value: string | null;
  anchor: string | null;
}

export interface DocMappingField {
  id: string;
  sheet_name: string;
  column_key: string | null;
  excel_name: string;
  display_label: string | null;
  source_document: string | null;
  samples: string[];
  data_type: string;
  mandatory: boolean;
  mark: DocMark | null;
}

export interface DocMappingDocument {
  id: string;
  name: string;
  sample: { file_name: string; page_count: number; uploaded_by: string | null; updated_at: string | null } | null;
  template_sample_available: boolean;
}

export interface DocMapping {
  workbook: { id: string; file_name: string; status: string };
  documents: DocMappingDocument[];
  fields: DocMappingField[];
}

export async function getDocMapping(workbookId: string): Promise<DocMapping> {
  const { data } = await apiClient.get<DocMapping>(`/onboarding/erp/workbooks/${workbookId}/doc-mapping`);
  return data;
}

export async function uploadSample(workbookId: string, docId: string, file: File): Promise<DocMapping> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<DocMapping>(`/onboarding/erp/workbooks/${workbookId}/documents/${docId}/sample`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function sampleFromTemplate(workbookId: string, docId: string): Promise<DocMapping> {
  const { data } = await apiClient.post<DocMapping>(`/onboarding/erp/workbooks/${workbookId}/documents/${docId}/sample/from-template`);
  return data;
}

export async function fetchSamplePageUrl(workbookId: string, docId: string, page: number): Promise<string> {
  const { data } = await apiClient.get<Blob>(`/onboarding/erp/workbooks/${workbookId}/documents/${docId}/sample/pages/${page}`, {
    responseType: "blob",
  });
  return URL.createObjectURL(data);
}

export async function saveMark(fieldId: string, box: { page: number; x: number; y: number; width: number; height: number; line_item: boolean }): Promise<DocMark> {
  const { data } = await apiClient.put<{ mark: DocMark }>(`/onboarding/erp/fields/${fieldId}/mark`, box);
  return data.mark;
}

export async function setMarkLineItem(fieldId: string, lineItem: boolean): Promise<DocMark> {
  const { data } = await apiClient.patch<{ mark: DocMark }>(`/onboarding/erp/fields/${fieldId}/mark`, { line_item: lineItem });
  return data.mark;
}

export async function clearMark(fieldId: string): Promise<void> {
  await apiClient.delete(`/onboarding/erp/fields/${fieldId}/mark`);
}

export interface ImportResult {
  classified: number;
  by_category: Partial<Record<ErpCategory, number>>;
  left_unclassified: number;
  boxes: number;
  samples_used: number;
  missing_fields: string[];
}

/** Fill Steps 1, 2 and 4 from the template's own ERP Excel mapping (reads the template only). */
export async function importCurrent(groupId: string, replace = false): Promise<ImportResult> {
  const { data } = await apiClient.post<ImportResult>(`/onboarding/erp/groups/${groupId}/import-current`, null, { params: { replace } });
  return data;
}
