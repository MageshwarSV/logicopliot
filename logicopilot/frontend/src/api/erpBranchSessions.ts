import { apiClient } from "./client";

/** One always-on ERP login for one branch - see backend app/core/branch_sessions.py. */
export interface BranchLogin {
  id: string;
  tenant_id: string;
  branch: string;
  login_script_id: string;
  username: string | null;
  has_password: boolean;
  max_tabs: number;
  enabled: boolean;
  /** starting | logging_in | live | error | off */
  status: string;
  status_note: string | null;
  last_check_at: string | null;
  live_since: string | null;
}

export interface BranchLoginInput {
  branch: string;
  login_script_id: string;
  username?: string | null;
  /** Omit to keep the saved password; "" clears it (the login script's own is used). */
  password?: string;
  max_tabs: number;
  enabled: boolean;
}

export async function listBranchLogins(tenantId: string): Promise<BranchLogin[]> {
  const { data } = await apiClient.get<BranchLogin[]>("/erp-branch-sessions", { params: { tenant_id: tenantId } });
  return data;
}

export async function createBranchLogin(tenantId: string, body: BranchLoginInput): Promise<BranchLogin> {
  const { data } = await apiClient.post<BranchLogin>("/erp-branch-sessions", body, { params: { tenant_id: tenantId } });
  return data;
}

export async function updateBranchLogin(id: string, body: Partial<BranchLoginInput>): Promise<BranchLogin> {
  const { data } = await apiClient.patch<BranchLogin>(`/erp-branch-sessions/${id}`, body);
  return data;
}

export async function deleteBranchLogin(id: string): Promise<void> {
  await apiClient.delete(`/erp-branch-sessions/${id}`);
}

/** Check the login now; log in again if it has dropped. */
export async function checkBranchLogin(id: string): Promise<BranchLogin> {
  const { data } = await apiClient.post<BranchLogin>(`/erp-branch-sessions/${id}/relogin`);
  return data;
}
