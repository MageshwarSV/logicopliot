import { apiClient } from "./client";

/** Auto ERP Submission - a job goes to the ERP straight after extraction, no review in Cargora.
 *  Allowed per tenant by the SaaS Admin; switched on per template (and branch) by the Tenant Admin. */
export interface AutoErpTemplate {
  group_id: string;
  name: string;
  mode: string | null;
  excel_entry: boolean;
  script: { id: string; name: string; branch_field: string | null } | null;
  enabled: boolean;
  /** Empty = every branch. */
  branches: string[];
  /** Why auto submission cannot really work for this template yet (no ready script, not Excel entry). */
  problems: string[];
}

export interface AutoErpSettings {
  tenant_id: string;
  tenant_name: string;
  allowed: boolean;
  /** Branches that have an ERP branch login - offered as choices. */
  branch_options: string[];
  templates: AutoErpTemplate[];
}

export async function getAutoErp(tenantId: string): Promise<AutoErpSettings> {
  return (await apiClient.get<AutoErpSettings>(`/auto-erp/tenants/${tenantId}`)).data;
}

export async function setAutoErpAllowed(tenantId: string, allowed: boolean): Promise<AutoErpSettings> {
  return (await apiClient.put<AutoErpSettings>(`/auto-erp/tenants/${tenantId}/allowed`, { allowed })).data;
}

export async function setAutoErpTemplate(
  tenantId: string, groupId: string, enabled: boolean, branches: string[],
): Promise<AutoErpSettings> {
  return (await apiClient.put<AutoErpSettings>(`/auto-erp/tenants/${tenantId}/templates/${groupId}`, { enabled, branches })).data;
}
