import { apiClient } from "./client";
import type { ReferenceUploadResult } from "./onboarding";

/** Tenant Admin -> Master Data -> ERP Master Data. See backend app/api/v1/master_data.py. */
export type MasterDataCategory = "importer" | "quotation";

export interface MasterDataTableDef {
  key: string;
  title: string;
  description: string;
  /** Headers for match_values[0..n-1]. */
  columns: string[];
  /** Header for resolved_value. */
  value: string;
}

export interface MasterDataTemplate {
  group_id: string;
  name: string;
  tables: Record<string, { field_id: string; rows: number }>;
}

export interface MasterDataOverview {
  tables: MasterDataTableDef[];
  templates: MasterDataTemplate[];
}

export interface MasterDataRow {
  id: number;
  match_values: string[];
  resolved_value: string;
  updated_at: string | null;
}

export interface MasterDataPage {
  total: number;
  page: number;
  page_size: number;
  rows: MasterDataRow[];
}

const base = (category: MasterDataCategory, fieldId: string) => `/master-data/${category}/fields/${fieldId}`;

export async function getOverview(category: MasterDataCategory): Promise<MasterDataOverview> {
  const { data } = await apiClient.get<MasterDataOverview>(`/master-data/${category}`);
  return data;
}

export async function listRows(
  category: MasterDataCategory, fieldId: string, params: { q?: string; page: number; page_size: number },
): Promise<MasterDataPage> {
  const { data } = await apiClient.get<MasterDataPage>(`${base(category, fieldId)}/rows`, { params });
  return data;
}

export async function addRows(
  category: MasterDataCategory, fieldId: string, rows: { match_values: string[]; resolved_value: string }[],
): Promise<ReferenceUploadResult> {
  const { data } = await apiClient.post<ReferenceUploadResult>(`${base(category, fieldId)}/rows`, { rows });
  return data;
}

export async function updateRow(
  category: MasterDataCategory, fieldId: string, rowId: number, resolvedValue: string,
): Promise<MasterDataRow> {
  const { data } = await apiClient.patch<MasterDataRow>(`${base(category, fieldId)}/rows/${rowId}`, {
    resolved_value: resolvedValue,
  });
  return data;
}

export async function deleteRow(category: MasterDataCategory, fieldId: string, rowId: number): Promise<void> {
  await apiClient.delete(`${base(category, fieldId)}/rows/${rowId}`);
}

export async function uploadRows(category: MasterDataCategory, fieldId: string, file: File): Promise<ReferenceUploadResult> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<ReferenceUploadResult>(`${base(category, fieldId)}/upload`, form);
  return data;
}

/** Fetch a workbook (template or full export) and hand it to the browser as a download. */
export async function downloadWorkbook(
  category: MasterDataCategory, fieldId: string, kind: "template" | "export",
): Promise<void> {
  const { data, headers } = await apiClient.get<Blob>(`${base(category, fieldId)}/${kind}`, { responseType: "blob" });
  const match = /filename="?([^"]+)"?/.exec(String(headers["content-disposition"] ?? ""));
  const url = URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url;
  a.download = match?.[1] ?? `${kind}.xlsx`;
  a.click();
  URL.revokeObjectURL(url);
}
