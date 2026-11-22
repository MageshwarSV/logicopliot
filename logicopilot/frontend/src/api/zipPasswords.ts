import { apiClient } from "./client";

/** A saved zip password - the password itself is never sent back after it is saved. */
export interface ZipPassword {
  id: string;
  label: string;
  group_id?: string | null;
  group_name?: string | null;
  sender_domains?: string[] | null;
  last_used_at?: string | null;
  use_count: number;
  created_at?: string | null;
}

export async function listZipPasswords(): Promise<ZipPassword[]> {
  const { data } = await apiClient.get<ZipPassword[]>("/zip-passwords");
  return data;
}

export async function addZipPassword(body: {
  label: string; password: string; group_id?: string | null; sender_domains?: string[] | null;
}): Promise<ZipPassword> {
  const { data } = await apiClient.post<ZipPassword>("/zip-passwords", body);
  return data;
}

export async function deleteZipPassword(id: string): Promise<void> {
  await apiClient.delete(`/zip-passwords/${id}`);
}
