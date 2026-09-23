import { apiClient } from "./client";

export interface InboxItem {
  id: string;
  kind: "change_request" | "erp_access" | "job_failure";
  group_id?: string | null;
  job_id?: string | null;
  group_name?: string | null;
  tenant_name: string;
  raised_by: string | null;
  message: string;
  status: string;
  created_at: string;
  erp_url?: string | null;
  erp_username?: string | null;
  erp_password?: string | null;
}

// --- Super Admin inbox ---
export async function listInbox(): Promise<InboxItem[]> {
  const { data } = await apiClient.get<InboxItem[]>("/template-reviews");
  return data;
}

export async function resolveReview(reviewId: string): Promise<void> {
  await apiClient.post(`/template-reviews/${reviewId}/resolve`);
}

// --- ERP access requests (Tenant Admin submits → Super Admin inbox) ---
export async function submitErpAccess(payload: { url: string; username: string; password: string }): Promise<void> {
  await apiClient.post("/erp-access-requests", payload);
}

export async function resolveErpAccess(requestId: string): Promise<void> {
  await apiClient.post(`/erp-access-requests/${requestId}/resolve`);
}

export async function resolveJobFailure(failureId: string): Promise<void> {
  await apiClient.post(`/job-failures/${failureId}/resolve`);
}

// --- Tenant Admin actions ---
export async function approveTemplate(groupId: string): Promise<void> {
  await apiClient.post(`/template-groups/${groupId}/approve`);
}

export async function requestChanges(groupId: string, message: string): Promise<void> {
  await apiClient.post(`/template-groups/${groupId}/request-changes`, { message });
}

export async function setFormatPrompt(markId: string, formatPrompt: string): Promise<void> {
  await apiClient.patch(`/marks/${markId}/format`, { format_prompt: formatPrompt });
}

export async function formatCheck(
  markId: string,
  payload: { value?: string | null; format_prompt: string },
): Promise<string> {
  const { data } = await apiClient.post<{ formatted_value: string }>(
    `/marks/${markId}/format-check`,
    payload,
  );
  return data.formatted_value;
}
