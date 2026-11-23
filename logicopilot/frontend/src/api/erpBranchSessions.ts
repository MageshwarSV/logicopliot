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
  /** Stream scripts allowed to run their jobs on this login; empty = all scripts. */
  script_ids: string[];
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
  script_ids?: string[];
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

/** A branch login as a user it was assigned to sees it - never the ERP username or password. */
export interface BranchSwitch {
  id: string;
  branch: string;
  enabled: boolean;
  /** starting | logging_in | live | error | off */
  status: string;
  status_note: string | null;
  live_since: string | null;
}

export interface UserBranchAccess {
  user_id: string;
  /** The branch logins this user may turn on and off. */
  branch_session_ids: string[];
  /** Every branch login of the user's company, to tick from. */
  branches: BranchSwitch[];
}

/** Super Admin: the branch logins of this user's company and which ones the user may switch. */
export async function getUserBranchAccess(userId: string): Promise<UserBranchAccess> {
  const { data } = await apiClient.get<UserBranchAccess>(`/erp-branch-sessions/user-access/${userId}`);
  return data;
}

/** Super Admin: replace which branch logins this user may switch (empty = none). */
export async function setUserBranchAccess(userId: string, branchSessionIds: string[]): Promise<UserBranchAccess> {
  const { data } = await apiClient.put<UserBranchAccess>(`/erp-branch-sessions/user-access/${userId}`, {
    branch_session_ids: branchSessionIds,
  });
  return data;
}

/** The branch logins the signed-in user may turn on and off (the top-bar "ERP Branch" button). */
export async function myBranchSwitches(): Promise<BranchSwitch[]> {
  const { data } = await apiClient.get<BranchSwitch[]>("/erp-branch-sessions/mine");
  return data;
}

/** Turn one branch login on or off (a Super Admin, or a user it was assigned to). */
export async function switchBranchLogin(id: string, enabled: boolean): Promise<BranchSwitch> {
  const { data } = await apiClient.post<BranchSwitch>(`/erp-branch-sessions/${id}/switch`, { enabled });
  return data;
}
