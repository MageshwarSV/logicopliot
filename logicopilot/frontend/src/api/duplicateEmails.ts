import { apiClient } from "./client";

/** A pre-alert that announced a Bill of Lading this customer had already sent, held instead
 *  of becoming a job. Nothing has been read out of it - no attachment downloaded, no page
 *  OCR'd - so the whole decision is still the operator's. */
export interface DuplicateEmail {
  id: string;
  message_id: string;
  sender: string | null;
  /** The customer, taken from the ORIGINAL job - this mail was never classified. */
  customer: string | null;
  /** That customer's shipment type ("Sea Import", ...), for the tabs. Same source. */
  mode: string | null;
  subject: string | null;
  received_at: string | null;
  bl_number: string;
  original_job_id: string | null;
  original_reference: string | null;
  original_received_at: string | null;
  status: string;
  /** False once the kept copy of the message is gone, which is the only thing
   *  "Process as new job" has to work from. */
  can_process: boolean;
}

export async function listDuplicateEmails(bl?: string): Promise<DuplicateEmail[]> {
  const { data } = await apiClient.get<DuplicateEmail[]>("/duplicate-emails", {
    params: bl ? { bl } : undefined,
  });
  return data;
}

/** It really was a re-send: drop it, and the kept copy of the mail with it. */
export async function deleteDuplicateEmail(id: string): Promise<DuplicateEmail> {
  const { data } = await apiClient.delete<DuplicateEmail>(`/duplicate-emails/${id}`);
  return data;
}

/** It really is a separate shipment: run the kept message through the ordinary pipeline,
 *  exactly as if it had just arrived. Takes as long as any new job does. */
export async function processDuplicateEmail(id: string): Promise<DuplicateEmail> {
  const { data } = await apiClient.post<DuplicateEmail>(`/duplicate-emails/${id}/process`);
  return data;
}
