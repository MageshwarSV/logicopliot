import { apiClient } from "./client";

export interface EmailStatus {
  ok: boolean;
  user?: string;
  mailbox?: string;
  total?: number;
  unseen?: number;
  error?: string;
}

export interface PulledMessage {
  from: string;
  subject: string;
  matched: string | null;
  matched_email?: string;
  reason?: string;
  job_id?: string;
  filled_slots?: string[];
  attachments?: number;
}

export interface PullResult {
  ok: boolean;
  processed?: PulledMessage[];
  note?: string;
  error?: string;
}

export async function emailStatus(): Promise<EmailStatus> {
  const { data } = await apiClient.get<EmailStatus>("/email/status");
  return data;
}

export async function pullEmail(): Promise<PullResult> {
  const { data } = await apiClient.post<PullResult>("/email/pull");
  return data;
}

/** A mail that was tried and could not be read because the AI service was unavailable. */
export interface HeldEmail {
  id: string;
  message_id: string;
  sender: string | null;
  subject: string | null;
  template: string | null;
  reason: string | null;
  first_failed_at: string | null;
  last_tried_at: string | null;
}

export interface HeldEmails {
  /** The puller is holding off and opening no mail until `until`. */
  waiting: boolean;
  until: string | null;
  reason: string;
  total: number;
  items: HeldEmail[];
}

export async function listHeldEmails(): Promise<HeldEmails> {
  const { data } = await apiClient.get<HeldEmails>("/email/failed");
  return data;
}

/** Stop waiting and pull again now. Returns how many mails were held. */
export async function rerunHeldEmails(): Promise<{ queued: number }> {
  const { data } = await apiClient.post<{ queued: number }>("/email/failed/rerun");
  return data;
}
