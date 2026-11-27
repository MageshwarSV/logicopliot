import { apiClient } from "./client";
import type { ScreenLayout } from "../types/jobs";

export interface DesignerTemplate {
  id: string;
  name: string;
  mode: string | null;
  tenant_id: string;
  documents: { id: string; name: string; doc_type: string; has_layout: boolean }[];
}

export interface DesignerField {
  ref: string;
  id: string;
  source: "mark" | "custom";
  place: "section" | "card";
  label_name: string;
  display_label: string | null;
  full_width: boolean;
  quarter_width: boolean;
  multiline: boolean;
  mandatory: boolean;
  fixed_options: string[] | null;
  dropdown_style: string | null;
  kind?: string;
  on_document?: boolean;
  on_this_tab?: boolean;
}

export interface LayoutResponse {
  layout: ScreenLayout | null;
  updated_at: string | null;
  updated_by: string | null;
  dropped?: string[];
}

export interface RecentJob {
  id: string;
  reference: string;
  status: string;
  created_at: string | null;
}

export async function listTemplates(): Promise<DesignerTemplate[]> {
  const { data } = await apiClient.get<DesignerTemplate[]>("/screen-designer/templates");
  return data;
}

export async function listFields(documentId: string): Promise<{ fields: DesignerField[] }> {
  const { data } = await apiClient.get(`/screen-designer/documents/${documentId}/fields`);
  return data;
}

export async function getLayout(documentId: string): Promise<LayoutResponse> {
  const { data } = await apiClient.get<LayoutResponse>(`/screen-designer/documents/${documentId}/layout`);
  return data;
}

export async function saveLayout(documentId: string, layout: ScreenLayout): Promise<LayoutResponse> {
  const { data } = await apiClient.put<LayoutResponse>(`/screen-designer/documents/${documentId}/layout`, { layout });
  return data;
}

export async function createFromCurrent(documentId: string): Promise<LayoutResponse> {
  const { data } = await apiClient.post<LayoutResponse>(`/screen-designer/documents/${documentId}/layout/from-current`);
  return data;
}

export async function removeLayout(documentId: string): Promise<LayoutResponse> {
  const { data } = await apiClient.delete<LayoutResponse>(`/screen-designer/documents/${documentId}/layout`);
  return data;
}

export async function recentJobs(groupId: string): Promise<RecentJob[]> {
  const { data } = await apiClient.get<RecentJob[]>(`/screen-designer/groups/${groupId}/recent-jobs`);
  return data;
}

/** Field properties are saved on the field itself - the same settings the template wizard edits. */
export type FieldPatch = Partial<{
  display_label: string | null;
  full_width: boolean;
  quarter_width: boolean;
  multiline: boolean;
  ask_operator_required: boolean;
  fixed_options: string[] | null;
  dropdown_style: string | null;
}>;

export async function patchField(field: DesignerField, patch: FieldPatch): Promise<void> {
  const url = field.source === "mark" ? `/marks/${field.id}` : `/custom-fields/${field.id}`;
  await apiClient.patch(url, patch);
}

/** Where a field's parameter (internal) name is used, and whether a safe rename is possible. */
export interface FieldUsage {
  name: string;
  auto: string[];
  blocks: string[];
  allowed: boolean;
}

export async function fieldUsage(groupId: string, ref: string): Promise<FieldUsage> {
  const { data } = await apiClient.get<FieldUsage>("/screen-designer/fields/usage", { params: { group_id: groupId, ref } });
  return data;
}

export async function renameParameter(groupId: string, ref: string, newName: string): Promise<{ name: string; renamed: number; excel_updated?: number }> {
  const { data } = await apiClient.post("/screen-designer/fields/rename", { group_id: groupId, ref, new_name: newName });
  return data;
}
