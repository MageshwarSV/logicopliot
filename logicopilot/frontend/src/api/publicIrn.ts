import apiClient from "./client";
import type { Job, JobDetail } from "../types/jobs";
import type { SupportingDocument } from "./jobs";

/** The two standalone IRN Pending pages' own API calls - hit app/api/v1/public_irn.py, the
 *  one place on the backend that accepts `key` instead of a login session. Kept apart from
 *  api/jobs.ts on purpose: those functions call the normal, session-authenticated /jobs
 *  endpoints, and mixing the two would make it easy to accidentally call an authenticated
 *  endpoint from a page that must work for someone who never logged in. */

export async function listIrnPending(key: string): Promise<Job[]> {
  const { data } = await apiClient.get<Job[]>("/public/irn-pending", { params: { key } });
  return data;
}

export async function getIrnPendingJob(jobId: string, key: string): Promise<JobDetail> {
  const { data } = await apiClient.get<JobDetail>(`/public/irn-pending/${jobId}`, { params: { key } });
  return data;
}

export async function listIrnPendingSupportingDocuments(
  jobId: string, key: string,
): Promise<SupportingDocument[]> {
  const { data } = await apiClient.get<{ documents: SupportingDocument[] }>(
    `/public/irn-pending/${jobId}/supporting-documents`,
    { params: { key } },
  );
  return data.documents;
}

export async function irnPendingDocPageUrl(
  jobId: string, jobDocumentId: string, page: number, key: string,
): Promise<string> {
  const { data } = await apiClient.get<Blob>(
    `/public/irn-pending/${jobId}/documents/${jobDocumentId}/pages/${page}`,
    { params: { key }, responseType: "blob" },
  );
  return URL.createObjectURL(data);
}

export async function irnPendingSupportingDocumentFileUrl(
  jobId: string, docId: string, storedAs: string, key: string,
): Promise<string> {
  const { data } = await apiClient.get<Blob>(
    `/public/irn-pending/${jobId}/supporting-documents/${docId}/files/${storedAs}`,
    { params: { key }, responseType: "blob" },
  );
  return URL.createObjectURL(data);
}

// ---- DSC + IRN Number: one document at a time, unsigned (left) vs signed (right) ----------

export interface IrnDocumentGroup {
  doc_ref: string;
  label: string;
  kind: "job_document" | "supporting_document";
  unsigned_files: Array<
    { job_document_id: string; page_count: number } | { stored_as: string; original_name: string }
  >;
  signed: { irn_number: string; original_name: string; stored_as: string; created_at: string } | null;
}

export async function listIrnPendingDocuments(jobId: string, key: string): Promise<IrnDocumentGroup[]> {
  const { data } = await apiClient.get<{ documents: IrnDocumentGroup[] }>(
    `/public/irn-pending/${jobId}/documents`,
    { params: { key } },
  );
  return data.documents;
}

export interface SignIrnDocumentResult {
  documents: IrnDocumentGroup[];
  job_status: string;
  all_signed: boolean;
}

export async function signIrnPendingDocument(
  jobId: string, docRef: string, irnNumber: string, file: File, key: string,
): Promise<SignIrnDocumentResult> {
  const form = new FormData();
  form.append("irn_number", irnNumber);
  form.append("file", file);
  const { data } = await apiClient.post<SignIrnDocumentResult>(
    `/public/irn-pending/${jobId}/documents/${docRef}/sign`,
    form,
    { params: { key } },
  );
  return data;
}

export async function irnPendingSignedFileUrl(jobId: string, docRef: string, key: string): Promise<string> {
  const { data } = await apiClient.get<Blob>(
    `/public/irn-pending/${jobId}/documents/${docRef}/signed-file`,
    { params: { key }, responseType: "blob" },
  );
  return URL.createObjectURL(data);
}
