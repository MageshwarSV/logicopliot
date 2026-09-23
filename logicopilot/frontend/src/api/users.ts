import { apiClient } from "./client";
import type { MailProvider, Role, User } from "../types/auth";

export async function listUsers(tenantId?: string): Promise<User[]> {
  const response = await apiClient.get<User[]>("/users", { params: tenantId ? { tenant_id: tenantId } : undefined });
  return response.data;
}

export async function createUser(payload: {
  email: string;
  password: string;
  full_name: string;
  role: Role;
  tenant_id?: string;
  template_ids?: string[];
  mail_provider?: MailProvider;
  mail_email?: string;
  mail_app_password?: string;
  // Shipment modes a GK2 user reviews — required for role "gk2", ignored otherwise.
  modes?: string[];
  // A Tenant Admin's own custom type to create this account under — its base_role wins
  // over `role` above server-side.
  user_type_id?: string;
}): Promise<User> {
  const response = await apiClient.post<User>("/users", payload);
  return response.data;
}

export async function setUserActive(userId: string, isActive: boolean): Promise<User> {
  const response = await apiClient.patch<User>(`/users/${userId}`, { is_active: isActive });
  return response.data;
}

export async function updateUser(
  userId: string,
  payload: {
    email?: string; full_name?: string; password?: string; is_active?: boolean; template_ids?: string[];
    mail_provider?: MailProvider; mail_email?: string; mail_app_password?: string;
    modes?: string[];
  },
): Promise<User> {
  const response = await apiClient.patch<User>(`/users/${userId}`, payload);
  return response.data;
}

export async function getUserTemplates(userId: string): Promise<string[]> {
  const response = await apiClient.get<{ template_ids: string[] }>(`/users/${userId}/templates`);
  return response.data.template_ids;
}

export async function deleteUser(userId: string): Promise<void> {
  await apiClient.delete(`/users/${userId}`);
}
