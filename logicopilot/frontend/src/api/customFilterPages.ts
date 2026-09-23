import { apiClient } from "./client";
import type { CustomFilterPage } from "../types/customFilterPage";

export async function listCustomFilterPages(): Promise<CustomFilterPage[]> {
  const { data } = await apiClient.get<CustomFilterPage[]>("/custom-filter-pages");
  return data;
}

export async function uploadCustomFilterPage(name: string, file: File): Promise<CustomFilterPage> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<CustomFilterPage>("/custom-filter-pages", form, {
    params: { name },
  });
  return data;
}

export async function setCustomFilterPageActive(id: string, isActive: boolean): Promise<CustomFilterPage> {
  const { data } = await apiClient.patch<CustomFilterPage>(`/custom-filter-pages/${id}/active`, {
    is_active: isActive,
  });
  return data;
}

export async function deleteCustomFilterPage(id: string): Promise<void> {
  await apiClient.delete(`/custom-filter-pages/${id}`);
}

export async function customFilterPageUrl(id: string, page = 1): Promise<string> {
  const { data } = await apiClient.get<Blob>(`/custom-filter-pages/${id}/pages/${page}`, {
    responseType: "blob",
  });
  return URL.createObjectURL(data);
}
