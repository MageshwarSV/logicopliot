import { apiClient } from "./client";
import type { Tenant } from "../types/tenant";

export async function listTenants(): Promise<Tenant[]> {
  const response = await apiClient.get<Tenant[]>("/tenants");
  return response.data;
}

export async function getTenant(tenantId: string): Promise<Tenant> {
  const response = await apiClient.get<Tenant>(`/tenants/${tenantId}`);
  return response.data;
}

export async function deleteTenant(tenantId: string): Promise<void> {
  await apiClient.delete(`/tenants/${tenantId}`);
}

/** "Generate Link" on the Super Admin dashboard - creates the tenant's IRN Pending key the
 *  first time this is called for them, or just returns the existing one on every call after
 *  that (never rotates it). */
export async function generateIrnPendingKey(tenantId: string): Promise<Tenant> {
  const response = await apiClient.post<Tenant>(`/tenants/${tenantId}/irn-pending-key`);
  return response.data;
}

/** Tenant Admin manages their own tenant's allowed_modes (the "Shipment Type" page); Super
 *  Admin can use it for any tenant. Empty array clears the restriction (unrestricted). */
export async function updateTenant(
  tenantId: string,
  payload: { allowed_modes?: string[]; role_write_enabled?: Record<string, boolean> },
): Promise<Tenant> {
  const response = await apiClient.patch<Tenant>(`/tenants/${tenantId}`, payload);
  return response.data;
}

export async function createTenant(payload: {
  name: string;
  region?: string;
  currency?: string;
  /** Transport modes this client is licensed for. Omit/empty = unrestricted. */
  allowed_modes?: string[];
  admin_full_name?: string;
  admin_email?: string;
  admin_password?: string;
}): Promise<Tenant> {
  const response = await apiClient.post<Tenant>("/tenants", payload);
  return response.data;
}
