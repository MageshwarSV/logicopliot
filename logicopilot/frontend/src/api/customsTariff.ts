import { apiClient } from "./client";

// Indian Customs Tariff (ICEGATE) - the global CTH master (backend app/api/v1/customs_tariff.py).
const BASE = "/customs-tariff";

export interface TariffCode {
  cth: string;
  description: string | null;
  rate: string | null;
  unit: string | null;
  preferential_rate: string | null;
  is_active: boolean;
  deactivated_at: string | null;
}

export interface TariffUpload {
  id: string;
  file_name: string;
  uploaded_by: string | null;
  uploaded_at: string | null;
  total_codes: number;
  added: number;
  changed: number;
  deactivated: number;
  reactivated: number;
  skipped_rows: number;
}

export interface TariffSummary {
  active: number;
  inactive: number;
  with_description: number;
  last_upload: TariffUpload | null;
}

export interface TariffPreview {
  total_codes: number;
  columns: string[];
  added: number;
  changed: number;
  reactivated: number;
  deactivated: number;
  active_now: number;
  examples_added: string[];
  examples_deactivated: string[];
  examples_changed: { cth: string; changes: Record<string, { from: string | null; to: string | null }> }[];
  skipped_rows: number;
  examples_skipped: { row: number; value: string; reason: string }[];
  large_removal: boolean;
}

export async function getTariffSummary(): Promise<TariffSummary> {
  const { data } = await apiClient.get<TariffSummary>(`${BASE}/summary`);
  return data;
}

export async function searchTariff(params: { q?: string; include_inactive?: boolean; page: number; page_size: number }): Promise<{ total: number; rows: TariffCode[] }> {
  const { data } = await apiClient.get<{ total: number; rows: TariffCode[] }>(`${BASE}/codes`, { params });
  return data;
}

export async function listTariffUploads(): Promise<TariffUpload[]> {
  const { data } = await apiClient.get<{ uploads: TariffUpload[] }>(`${BASE}/uploads`);
  return data.uploads;
}

function form(file: File): FormData {
  const f = new FormData();
  f.append("file", file);
  return f;
}

export async function previewTariffUpload(file: File): Promise<TariffPreview> {
  const { data } = await apiClient.post<TariffPreview>(`${BASE}/upload/preview`, form(file));
  return data;
}

export async function applyTariffUpload(file: File): Promise<TariffPreview & { upload_id: string }> {
  const { data } = await apiClient.post<TariffPreview & { upload_id: string }>(`${BASE}/upload`, form(file));
  return data;
}

export async function downloadTariff(includeInactive = false): Promise<void> {
  const { data } = await apiClient.get<Blob>(`${BASE}/download`, {
    params: { include_inactive: includeInactive }, responseType: "blob",
  });
  const url = URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url;
  a.download = "Indian_Customs_Tariff.xlsx";
  a.click();
  URL.revokeObjectURL(url);
}

export interface TariffTryResult {
  cth: string;
  how: "document" | "ai" | "fallback" | "";
  confidence: string;
  note: string;
  code: TariffCode | null;
}

export async function tryTariffMatch(body: { doc_cth: string; part_code: string; description: string }): Promise<TariffTryResult> {
  const { data } = await apiClient.post<TariffTryResult>(`${BASE}/try`, body);
  return data;
}

export interface DumpCheck {
  group_id: string;
  template: string;
  tenant: string;
  ready: boolean;
  rows?: number;
  rows_with_cth?: number;
  distinct_codes?: number;
  codes_not_in_tariff?: number;
  rows_not_in_tariff?: number;
  examples?: { cth: string; part_code: string; description: string }[];
}

export async function getDumpCheck(): Promise<DumpCheck[]> {
  const { data } = await apiClient.get<{ templates: DumpCheck[] }>(`${BASE}/dump-check`);
  return data.templates;
}
