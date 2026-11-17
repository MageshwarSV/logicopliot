import { apiClient } from "./client";

// --- ERP Master Data - Importer: one row per importer branch, by country (master_data_importer.py).
const IMP = "/master-data/importer-directory";

export interface ImporterTemplate {
  group_id: string;
  name: string;
  countries: { country: string; rows: number; gstin_rows: number }[];
}

export interface ImporterRow {
  id: number;
  name: string;
  branch: string;
  country: string;
  gstin: string | null;
  address: string;
  is_active: boolean;
  updated_at: string | null;
}

export interface ImporterUploadConflict {
  id: number;
  field: "address" | "gstin";
  name: string;
  branch: string;
  country: string;
  existing_value: string;
  uploaded_value: string;
}

export interface ImporterUploadResult {
  rows_added: number;
  gstin_filled: number;
  rows_unchanged: number;
  rows_skipped: number;
  conflicts: ImporterUploadConflict[];
  conflict_count: number;
}

export async function listImporterTemplates(): Promise<ImporterTemplate[]> {
  const { data } = await apiClient.get<{ templates: ImporterTemplate[] }>(IMP);
  return data.templates;
}

export async function listImporterRows(
  groupId: string,
  params: { country: string; q?: string; gstin_only: boolean; sort: "name" | "branch"; direction: "asc" | "desc"; page: number; page_size: number },
): Promise<{ total: number; rows: ImporterRow[] }> {
  const { data } = await apiClient.get<{ total: number; rows: ImporterRow[] }>(`${IMP}/${groupId}/rows`, { params });
  return data;
}

export async function addImporterRow(
  groupId: string, row: { country: string; name: string; branch: string; gstin: string; address: string },
): Promise<ImporterRow> {
  const { data } = await apiClient.post<ImporterRow>(`${IMP}/${groupId}/rows`, row);
  return data;
}

export async function updateImporterRow(
  groupId: string, rowId: number, patch: { address?: string; gstin?: string },
): Promise<ImporterRow> {
  const { data } = await apiClient.patch<ImporterRow>(`${IMP}/${groupId}/rows/${rowId}`, patch);
  return data;
}

/** Records are never deleted: a branch is made Inactive (hidden from jobs) or Active again. */
export async function setImporterRowActive(groupId: string, rowId: number, isActive: boolean): Promise<ImporterRow> {
  const { data } = await apiClient.put<ImporterRow>(`${IMP}/${groupId}/rows/${rowId}/active`, { is_active: isActive });
  return data;
}

export async function uploadImporterRows(groupId: string, file: File): Promise<ImporterUploadResult> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<ImporterUploadResult>(`${IMP}/${groupId}/upload`, form);
  return data;
}

export async function downloadImporterWorkbook(
  groupId: string, kind: "template" | "export", country?: string, gstinOnly = false,
): Promise<void> {
  const { data, headers } = await apiClient.get<Blob>(`${IMP}/${groupId}/${kind}`, {
    responseType: "blob",
    params: kind === "export" ? { country, gstin_only: gstinOnly } : undefined,
  });
  const match = /filename="?([^"]+)"?/.exec(String(headers["content-disposition"] ?? ""));
  const url = URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url;
  a.download = match?.[1] ?? `importers_${kind}.xlsx`;
  a.click();
  URL.revokeObjectURL(url);
}

// --- ERP Master Data - Quotation: one row per ERP quotation name + branch (master_data_quotation.py).
const QUO = "/master-data/quotation-directory";

export interface QuotationTemplate {
  group_id: string;
  name: string;
  rows: number;
}

export interface QuotationRow {
  id: number;
  name: string;
  branch: string;
  address: string;
  is_active: boolean;
  updated_at: string | null;
}

export async function listQuotationTemplates(): Promise<QuotationTemplate[]> {
  const { data } = await apiClient.get<{ templates: QuotationTemplate[] }>(QUO);
  return data.templates;
}

export async function listQuotationRows(
  groupId: string,
  params: { q?: string; sort: "name" | "branch"; direction: "asc" | "desc"; page: number; page_size: number },
): Promise<{ total: number; rows: QuotationRow[] }> {
  const { data } = await apiClient.get<{ total: number; rows: QuotationRow[] }>(`${QUO}/${groupId}/rows`, { params });
  return data;
}

export async function addQuotationRow(groupId: string, row: { name: string; branch: string; address: string }): Promise<QuotationRow> {
  const { data } = await apiClient.post<QuotationRow>(`${QUO}/${groupId}/rows`, row);
  return data;
}

export async function updateQuotationRow(groupId: string, rowId: number, address: string): Promise<QuotationRow> {
  const { data } = await apiClient.patch<QuotationRow>(`${QUO}/${groupId}/rows/${rowId}`, { address });
  return data;
}

export async function setQuotationRowActive(groupId: string, rowId: number, isActive: boolean): Promise<QuotationRow> {
  const { data } = await apiClient.put<QuotationRow>(`${QUO}/${groupId}/rows/${rowId}/active`, { is_active: isActive });
  return data;
}

export async function downloadQuotationExport(groupId: string): Promise<void> {
  const { data, headers } = await apiClient.get<Blob>(`${QUO}/${groupId}/export`, { responseType: "blob" });
  const match = /filename="?([^"]+)"?/.exec(String(headers["content-disposition"] ?? ""));
  const url = URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url;
  a.download = match?.[1] ?? "quotations.xlsx";
  a.click();
  URL.revokeObjectURL(url);
}

// --- CTH Master: the customer's CTH dump workbook, per template (master_data_cth.py).
// The workbook is the data: edits and additions are written into it. No full replace from the UI.
const CTH = "/master-data/cth-directory";

export interface CthDumpStatus {
  attached: boolean;
  file_name: string | null;
  uploaded_at: string | null;
}

export interface CthTemplate extends CthDumpStatus {
  group_id: string;
  name: string;
  rows: number | null;
}

export interface CthValues {
  part_code: string;
  description: string;
  cth: string;
}

export interface CthRow extends CthValues {
  row: number;
  cth_as_written: string;
}

export interface CthRowsPage extends CthDumpStatus {
  ready: boolean;
  total: number;
  rows: CthRow[];
}

export interface CthAddResult {
  added: number;
  skipped: { row: string; part_code: string; reason: string }[];
}

export async function listCthTemplates(): Promise<CthTemplate[]> {
  const { data } = await apiClient.get<{ templates: CthTemplate[] }>(CTH);
  return data.templates;
}

export async function listCthRows(
  groupId: string,
  params: { q?: string; sort: "part" | "description" | "cth"; direction: "asc" | "desc"; page: number; page_size: number },
): Promise<CthRowsPage> {
  const { data } = await apiClient.get<CthRowsPage>(`${CTH}/${groupId}/rows`, { params });
  return data;
}

export async function editCthRow(groupId: string, row: number, expected: CthValues, values: CthValues): Promise<CthRow> {
  const { data } = await apiClient.put<CthRow>(`${CTH}/${groupId}/rows/${row}`, { expected, values });
  return data;
}

export async function addCthRow(groupId: string, values: CthValues): Promise<void> {
  await apiClient.post(`${CTH}/${groupId}/rows`, values);
}

export async function addCthRowsFromFile(groupId: string, file: File): Promise<CthAddResult> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<CthAddResult>(`${CTH}/${groupId}/rows/upload`, form);
  return data;
}

export async function downloadCthFile(groupId: string, kind: "download" | "template"): Promise<void> {
  const { data, headers } = await apiClient.get<Blob>(`${CTH}/${groupId}/${kind}`, { responseType: "blob" });
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(String(headers["content-disposition"] ?? ""));
  const url = URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url;
  a.download = match?.[1] ? decodeURIComponent(match[1]) : kind === "template" ? "CTH_add_rows_template.xlsx" : "CTH_dump.xlsx";
  a.click();
  URL.revokeObjectURL(url);
}
