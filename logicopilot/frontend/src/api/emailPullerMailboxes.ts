import { apiClient } from "./client";
import type { MailProvider } from "../types/auth";

export interface ConnectedMailbox {
  id: string;
  provider: MailProvider;
  email: string;
  tenant_id: string | null;
  tenant_name: string | null;
  group_id: string | null;
  group_name: string | null;
  paused: boolean;
}

/** Every mailbox connected anywhere on the platform - independent of any operator user. A
 *  freshly-connected mailbox has tenant_id/group_id both null ("Unassigned") until a Super
 *  Admin picks them here. */
export async function listMailboxes(): Promise<ConnectedMailbox[]> {
  const { data } = await apiClient.get<ConnectedMailbox[]>("/email-puller/mailboxes");
  return data;
}

/** Verified live via IMAP before being saved (same check as the old per-user mailbox
 *  connect) - rejects with a 400 (message in err.response.data.detail) if the credentials
 *  don't actually work. provider omitted lets it auto-detect Gmail vs. Zoho from the app
 *  password alone. Lands as Unassigned (tenant_id/group_id both null). */
export async function connectMailbox(payload: {
  email: string;
  app_password: string;
  provider?: MailProvider;
}): Promise<ConnectedMailbox> {
  const { data } = await apiClient.post<ConnectedMailbox>("/email-puller/mailboxes", payload);
  return data;
}

/** Partial update - pass only the fields changing. Setting tenant_id clears group_id
 *  automatically if the tenant actually changed (a template belongs to one tenant). */
export async function updateMailbox(
  mailboxId: string,
  payload: { tenant_id?: string | null; group_id?: string | null; paused?: boolean },
): Promise<ConnectedMailbox> {
  const { data } = await apiClient.patch<ConnectedMailbox>(`/email-puller/mailboxes/${mailboxId}`, payload);
  return data;
}

export async function disconnectMailbox(mailboxId: string): Promise<void> {
  await apiClient.delete(`/email-puller/mailboxes/${mailboxId}`);
}
