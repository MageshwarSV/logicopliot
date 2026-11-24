import { apiClient } from "./client";

// Customer Onboarding - Step 1: ERP Field Classification.

export type ErpCategory = "document" | "calculated" | "erp" | "contact";
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
  erp_master_ref: string | null;
  contact_ref: string | null;
  /** Document fields: which of the workbook's documents it is read from (null = not assigned). */
  source_document: string | null;
  samples: string[];
}

export interface ErpDocument {
  id: string;
  name: string;
  template_document_id: string | null;
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
  "default_value" | "validation" | "formula" | "erp_master_ref" | "contact_ref" | "source_document">>;

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
