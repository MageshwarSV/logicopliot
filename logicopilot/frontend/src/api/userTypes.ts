import { apiClient } from "./client";

export interface UserType {
  id: string;
  name: string;
  base_role: "operator" | "gk2" | "manager";
  write_enabled: boolean;
}

export async function listUserTypes(): Promise<UserType[]> {
  const { data } = await apiClient.get<UserType[]>("/user-types");
  return data;
}

export async function createUserType(payload: {
  name: string;
  base_role: "operator" | "gk2" | "manager";
  write_enabled?: boolean;
}): Promise<UserType> {
  const { data } = await apiClient.post<UserType>("/user-types", payload);
  return data;
}

export async function updateUserType(id: string, writeEnabled: boolean): Promise<UserType> {
  const { data } = await apiClient.patch<UserType>(`/user-types/${id}`, { write_enabled: writeEnabled });
  return data;
}

export async function deleteUserType(id: string): Promise<void> {
  await apiClient.delete(`/user-types/${id}`);
}

/** Masters, Users, and the New Member form all pick from the same list — the 3 built-in
 *  types plus every custom type — and need the same "which one is this" encoding for a
 *  single <select>. A built-in role's own name IS its value; a custom type is `custom:<id>`,
 *  since two tenants (or two custom types) can share a display name but never an id. */
export type TypeSelection = "operator" | "gk2" | "manager" | `custom:${string}`;

export function customTypeIdOf(selection: string): string | null {
  return selection.startsWith("custom:") ? selection.slice("custom:".length) : null;
}

export function selectionFor(userTypeId: string | null | undefined, role: string): TypeSelection {
  return userTypeId ? (`custom:${userTypeId}` as TypeSelection) : (role as TypeSelection);
}
