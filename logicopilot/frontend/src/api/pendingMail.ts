import { apiClient } from "./client";

export interface PendingAttachment {
  name: string;
  ext: string;
  /** On-disk filename — what the download/preview URL needs, not the display name. */
  path: string;
}

export interface PendingEmail {
  id: string;
  message_id: string;
  sender: string | null;
  subject: string | null;
  reason: string | null;
  evidence: string | null;
  status: string;
  resolved_group_id: string | null;
  resolved_job_id: string | null;
  created_at: string;
  attachments: PendingAttachment[];
}

/** Mail the auto-router could not place on its own — held with its attachments for a
 *  person to pick the template by hand. Super Admin only: which tenant this even
 *  belongs to is exactly the thing nobody could tell automatically. */
export async function listPendingEmails(): Promise<PendingEmail[]> {
  const { data } = await apiClient.get<PendingEmail[]>("/pending-emails");
  return data;
}

export function pendingAttachmentUrl(pendingId: string, path: string): string {
  return `${apiClient.defaults.baseURL}/pending-emails/${pendingId}/attachments/${encodeURIComponent(path)}`;
}

export async function resolvePendingEmail(pendingId: string, groupId: string): Promise<PendingEmail> {
  const { data } = await apiClient.post<PendingEmail>(`/pending-emails/${pendingId}/resolve`, {
    group_id: groupId,
  });
  return data;
}

export async function dismissPendingEmail(pendingId: string): Promise<PendingEmail> {
  const { data } = await apiClient.post<PendingEmail>(`/pending-emails/${pendingId}/dismiss`);
  return data;
}
