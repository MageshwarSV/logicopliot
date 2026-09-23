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
