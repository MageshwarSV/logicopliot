import { apiClient } from "./client";
import type { Job, JobDetail, JobEvent, JobFieldValue } from "../types/jobs";

export interface AvailableGroup {
  id: string;
  tenant_id: string;
  name: string;
  status: string;
  /** Which transport mode this template is for — see MODES in types/onboarding.ts. */
  mode?: string | null;
  pull_email?: string | null;
  pull_operator_id?: string | null;
}

export async function listAvailableGroups(): Promise<AvailableGroup[]> {
  const { data } = await apiClient.get<AvailableGroup[]>("/available-groups");
  return data;
}

export interface ListJobsOptions {
  /** Only honoured for super_admin/admin, who see every tenant by default; every other role
   *  is always forced to their own tenant regardless. */
  tenantId?: string;
  /** Omit both to get everything in one call (what the dashboard's own box counts need).
   *  Given, the Jobs list pages through in batches instead of loading it all at once. */
  limit?: number;
  offset?: number;
  /** Matches one of the operator dashboard's boxes server-side — "pending" | "eta" |
   *  "approval", with bucket "today" | "week" | "month" | "all". Set together, from a box's
   *  own onClick, so the list shows exactly what was counted. */
  group?: string;
  bucket?: string;
  /** IrnPendingPage's own filter - jobs GK2 has parked in "IRN Document Process". See
   *  Job.gk2_status; independent of group/bucket. */
  gk2Status?: string;
  /** The Jobs list's own left-side screen - "new" | "submitted" | "completed". See
   *  Job.category / _category_condition in api/v1/jobs.py. Omitted (the default) returns
   *  every category, same as before this existed. */
  category?: string;
}

export async function listJobs(options: ListJobsOptions = {}): Promise<Job[]> {
  const { tenantId, limit, offset, group, bucket, gk2Status, category } = options;
  const { data } = await apiClient.get<Job[]>("/jobs", {
    params: {
      ...(tenantId ? { tenant_id: tenantId } : {}),
      ...(limit != null ? { limit, offset: offset ?? 0 } : {}),
      ...(group ? { group, bucket: bucket ?? "all" } : {}),
      ...(gk2Status ? { gk2_status: gk2Status } : {}),
      ...(category ? { category } : {}),
    },
  });
  return data;
}

/** Badge counts for the Jobs list's three left-side screens, under the caller's own
 *  tenant/role visibility - see GET /jobs/category-counts. */
export interface JobCategoryCounts {
  new: number;
  submitted: number;
  completed: number;
}

export async function getJobCategoryCounts(): Promise<JobCategoryCounts> {
  const { data } = await apiClient.get<JobCategoryCounts>("/jobs/category-counts");
  return data;
}

/** How far through its steps a run has got. `total` is 0 when nothing knows the length yet -
 *  a bar drawn off nothing is worse than no bar, so the screen leaves it out in that case. */
export interface RunProgress {
  done: number;
  total: number;
  percent: number;
}

export interface LiveRun {
  live: boolean;
  status: string;
  screenshot?: string | null;
  log: string[];
  progress?: RunProgress | null;
}

/** Where a running ERP entry has got to - the screen it is on, and the log so far. */
export async function jobLive(jobId: string): Promise<LiveRun> {
  const { data } = await apiClient.get<LiveRun>(`/jobs/${jobId}/live`);
  return data;
}

/** How far OCR has got on a job a single-template mailbox just created, before any document
 *  has landed in a slot - see app/core/email_progress.py. `active: false` once OCR has
 *  finished (classification has started, or the job already has documents) - poll this
 *  while a job has no uploaded documents yet instead of leaving Document Capture looking
 *  frozen for however long reading every page takes. */
export interface PullProgress {
  active: boolean;
  done?: number;
  total?: number;
  eta_seconds?: number | null;
}

export async function jobPullProgress(jobId: string): Promise<PullProgress> {
  const { data } = await apiClient.get<PullProgress>(`/jobs/${jobId}/pull-progress`);
  return data;
}

/** Everything that has happened to this job, oldest first. */
export async function jobHistory(jobId: string): Promise<JobEvent[]> {
  const { data } = await apiClient.get<JobEvent[]>(`/jobs/${jobId}/history`);
  return data;
}

export async function createJob(payload: { group_id: string; reference?: string }): Promise<Job> {
  const { data } = await apiClient.post<Job>("/jobs", payload);
  return data;
}

export async function deleteJob(jobId: string): Promise<void> {
  await apiClient.delete(`/jobs/${jobId}`);
}

export interface RulingResult {
  has_ruling: boolean;
  required_documents?: string[];
  all_documents?: string[];
  uploaded_documents?: string[];
  missing_documents?: string[];
  decision?: string | null;
  needs_input?: boolean;
  question?: string;
  /** Choices to offer the operator when the rule can't decide (e.g. incoterm codes). */
  options?: string[];
  /** True while the job is held waiting for that answer. */
  held?: boolean;
  reason?: string;
}

export async function evaluateRuling(jobId: string, answer?: string): Promise<RulingResult> {
  const { data } = await apiClient.post<RulingResult>(`/jobs/${jobId}/ruling`, { answer: answer ?? null });
  return data;
}

export async function rerunJob(jobId: string): Promise<JobDetail> {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/rerun`);
  return data;
}

export async function jobDocPageUrl(
  jobId: string,
  jobDocumentId: string,
  page: number,
  // Bumped (JobDocument.updated_at) whenever this slot's file is replaced. A same-slot
  // delete+reupload keeps the URL otherwise byte-identical to a page the browser may already
  // have cached from the file that was just deleted - this cache-busts it so a reupload is
  // guaranteed to hit the network, never a stale cached response for the old file.
  cacheBust?: string,
): Promise<string> {
  const { data } = await apiClient.get<Blob>(
    `/jobs/${jobId}/documents/${jobDocumentId}/pages/${page}`,
    { responseType: "blob", params: cacheBust ? { v: cacheBust } : undefined },
  );
  return URL.createObjectURL(data);
}

export async function getJob(jobId: string): Promise<JobDetail> {
  const { data } = await apiClient.get<JobDetail>(`/jobs/${jobId}`);
  return data;
}

export async function uploadJobDocument(
  jobId: string,
  templateDocumentId: string,
  file: File,
): Promise<JobDetail> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<JobDetail>(
    `/jobs/${jobId}/documents/${templateDocumentId}/upload`,
    form,
  );
  return data;
}

/** Remove ONE file from a slot — the wrong invoice, uploaded by mistake. */
export async function deleteJobDocumentFile(
  jobId: string,
  jobDocumentId: string,
): Promise<JobDetail> {
  const { data } = await apiClient.delete<JobDetail>(
    `/jobs/${jobId}/documents/${jobDocumentId}/file`,
  );
  return data;
}

export async function extractJob(jobId: string): Promise<JobDetail> {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/extract`);
  return data;
}

export interface SmartUploadResult {
  results: { filename: string; matched: string[] | null; error?: string }[];
  detail: JobDetail;
}

/** Accepts any number of files in one request — several PDFs picked at once, or a mix of
 *  PDFs and ZIPs. They MUST go up together: the slot-vs-slot specificity rule that decides
 *  which file wins a contested slot only works when the whole batch is classified as one. */
export async function smartUpload(jobId: string, files: File[]): Promise<SmartUploadResult> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  const { data } = await apiClient.post<SmartUploadResult>(`/jobs/${jobId}/smart-upload`, form);
  return data;
}

/** A page a smart-upload could not confidently place anywhere - kept instead of silently
 *  discarded, shown as a draggable card next to the document slots so an operator can resolve
 *  it by hand (see assignUnclassifiedPage). */
export interface UnclassifiedPage {
  id: string;
  original_filename: string;
  page_number: number;
  preview_url: string;
}

export async function getUnclassifiedPages(jobId: string): Promise<UnclassifiedPage[]> {
  const { data } = await apiClient.get<UnclassifiedPage[]>(`/jobs/${jobId}/unclassified-pages`);
  return data;
}

export async function unclassifiedPageImageUrl(jobId: string, pageId: string): Promise<string> {
  const { data } = await apiClient.get<Blob>(
    `/jobs/${jobId}/unclassified-pages/${pageId}/image`,
    { responseType: "blob" },
  );
  return URL.createObjectURL(data);
}

/** The operator drops an unclassified page onto a document slot label. Writes it into a real
 *  document for that slot and remembers it as a confirmed example so a similarly-worded
 *  document is recognised automatically next time. */
export async function assignUnclassifiedPage(
  jobId: string,
  pageId: string,
  templateDocumentId: string,
): Promise<JobDetail> {
  const { data } = await apiClient.post<JobDetail>(
    `/jobs/${jobId}/unclassified-pages/${pageId}/assign`,
    { template_document_id: templateDocumentId },
  );
  return data;
}

// ---- IRN Documents Upload (GK1) / IRN Processing (GK2): supporting documents + prealert ----

export interface SupportingDocumentFile {
  stored_as: string;
  original_name: string;
  size: number;
}

export interface SupportingDocument {
  id: string;
  label: string;
  files: SupportingDocumentFile[];
  /** Set instead of files for a row created via uploadSupportingDocumentOrReason when the
   *  operator gave a reason instead of uploading something - e.g. why no insurance premium
   *  certificate was attached. */
  reason_text: string | null;
  uploaded_by: string | null;
  created_at: string;
}

export async function listSupportingDocuments(jobId: string): Promise<SupportingDocument[]> {
  const { data } = await apiClient.get<{ documents: SupportingDocument[] }>(
    `/jobs/${jobId}/supporting-documents`,
  );
  return data.documents;
}

export async function uploadSupportingDocuments(
  jobId: string,
  label: string,
  files: File[],
): Promise<SupportingDocument> {
  const form = new FormData();
  form.append("label", label);
  for (const file of files) form.append("files", file);
  const { data } = await apiClient.post<SupportingDocument>(
    `/jobs/${jobId}/supporting-documents`,
    form,
  );
  return data;
}

/** Exactly one of `files` or `reason` - e.g. Insurance Rate (%)'s own save popup: upload the
 *  insurance premium certificate, or say why there isn't one. Stored the same way as any other
 *  supporting document, so it shows up on IRN Processing's own document list automatically. */
export async function uploadSupportingDocumentOrReason(
  jobId: string,
  customFieldId: string,
  label: string,
  opts: { files?: File[]; reason?: string },
): Promise<SupportingDocument> {
  const form = new FormData();
  form.append("label", label);
  if (opts.reason) form.append("reason", opts.reason);
  for (const file of opts.files ?? []) form.append("files", file);
  const { data } = await apiClient.post<SupportingDocument>(
    `/jobs/${jobId}/custom-fields/${customFieldId}/supporting-document-or-reason`,
    form,
  );
  return data;
}

export async function deleteSupportingDocument(jobId: string, docId: string): Promise<void> {
  await apiClient.delete(`/jobs/${jobId}/supporting-documents/${docId}`);
}

export async function supportingDocumentFileUrl(
  jobId: string,
  docId: string,
  storedAs: string,
): Promise<string> {
  const { data } = await apiClient.get<Blob>(
    `/jobs/${jobId}/supporting-documents/${docId}/files/${storedAs}`,
    { responseType: "blob" },
  );
  return URL.createObjectURL(data);
}

export async function skipIrnDocuments(jobId: string): Promise<{ irn_documents_done: boolean }> {
  const { data } = await apiClient.post<{ irn_documents_done: boolean }>(
    `/jobs/${jobId}/irn-documents/skip`,
  );
  return data;
}

export async function approveIrnDocuments(
  jobId: string,
): Promise<{ irn_documents_done: boolean; irn_approval_requested: boolean }> {
  const { data } = await apiClient.post<{ irn_documents_done: boolean; irn_approval_requested: boolean }>(
    `/jobs/${jobId}/irn-documents/approve`,
  );
  return data;
}

export interface PrealertAttachment {
  name: string;
  stored_as: string;
  size: number;
}

export interface Prealert {
  available: boolean;
  sender?: string | null;
  subject?: string | null;
  received_at?: string | null;
  has_original_eml?: boolean;
  attachments?: PrealertAttachment[];
}

export async function getPrealert(jobId: string): Promise<Prealert> {
  const { data } = await apiClient.get<Prealert>(`/jobs/${jobId}/prealert`);
  return data;
}

export async function prealertOriginalUrl(jobId: string): Promise<string> {
  const { data } = await apiClient.get<Blob>(`/jobs/${jobId}/prealert/original`, {
    responseType: "blob",
  });
  return URL.createObjectURL(data);
}

export async function prealertAttachmentUrl(jobId: string, storedAs: string): Promise<string> {
  const { data } = await apiClient.get<Blob>(`/jobs/${jobId}/prealert/attachments/${storedAs}`, {
    responseType: "blob",
  });
  return URL.createObjectURL(data);
}

/** "Download as Excel" - the same ERP-import workbook GK2's real approval builds, rebuilt
 *  fresh from the job's current data on every call. Throws (with a readable `detail`) if the
 *  job isn't set up for Excel entry, or the workbook would come out empty. */
export async function downloadJobExcelUrl(jobId: string): Promise<{ url: string; filename: string }> {
  const { data, headers } = await apiClient.get<Blob>(`/jobs/${jobId}/erp-excel`, {
    responseType: "blob",
  });
  const disposition = String(headers["content-disposition"] ?? "");
  const match = /filename="?([^"]+)"?/.exec(disposition);
  return { url: URL.createObjectURL(data), filename: match?.[1] ?? `${jobId}.xlsx` };
}

export async function completeJob(jobId: string): Promise<JobDetail> {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/complete`);
  return data;
}

export async function setVerificationDecision(
  jobId: string,
  linkId: string,
  accept: boolean,
): Promise<JobDetail> {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/verification-decision`, {
    link_id: linkId,
    accept,
  });
  return data;
}

export async function correctFieldValue(valueId: string, correctedValue: string): Promise<JobFieldValue> {
  const { data } = await apiClient.patch<JobFieldValue>(`/job-field-values/${valueId}`, {
    corrected_value: correctedValue,
  });
  return data;
}

export interface CustomFieldReferenceOption {
  value: string;
  /** This row's own match keys, in upload order (e.g. [port of entry, transport mode]) - so
   * the caller can narrow the list to the ones relevant to this job, against whichever of the
   * job's OTHER fields it already knows are the relevant keys. */
  match_values: string[];
}

/** Every row a reference-backed (is_target_value) custom field's table carries - e.g. every
 * real Customs House Code, alongside the keys it was uploaded under - so it can be offered as
 * a dropdown instead of only ever showing the one value an automatic lookup matched.
 *
 * `key`, when given, narrows SERVER-SIDE to rows whose first match value equals it (e.g. "ERP
 * Consignee Branch" - too large a table, tens of thousands of rows total, to ever fetch whole
 * the way CustomsHouseCode/branch_name do; pass the picked consignee name to get back only
 * that consignee's own branches). Omit it for a table small enough to fetch whole and filter
 * client-side, same as before. */
export async function listCustomFieldReferenceOptions(
  jobId: string, customFieldId: string, key?: string,
): Promise<CustomFieldReferenceOption[]> {
  const { data } = await apiClient.get<{ options: CustomFieldReferenceOption[] }>(
    `/jobs/${jobId}/custom-fields/${customFieldId}/reference-options`,
    key ? { params: { key } } : undefined,
  );
  return data.options;
}

/** Same as listCustomFieldReferenceOptions, resolved by the field's own label on this job's
 * template instead of a known custom_field_id - for a reference table backing a field that is
 * never itself asked/computed on a job (no JobFieldValue to read a custom_field_id off of),
 * e.g. ERP Quotation Address: looked up purely client-side from two OTHER fields' current
 * values. Resolves to an empty list (not an error) when this job's template has no such field -
 * most templates other than 4S Logistics' Sea Import, where this one is scoped. */
export async function listCustomFieldReferenceOptionsByLabel(
  jobId: string, label: string,
): Promise<CustomFieldReferenceOption[]> {
  try {
    const { data } = await apiClient.get<{ options: CustomFieldReferenceOption[] }>(
      `/jobs/${jobId}/custom-fields/by-label/${encodeURIComponent(label)}/reference-options`,
    );
    return data.options;
  } catch {
    return [];
  }
}

/** A suggested starting value for a plain ask_operator field, read off another field on the
 * SAME job (keyed_on_label) against this field's own reference table - the operator can still
 * pick anything else or type a new one, this is only ever a default. See Insurance Rate (%),
 * which starts pre-filled with a customer's own previously-quoted rate (fuzzy-matched on their
 * Consignee name, which rarely prints identically twice). */
export async function suggestCustomFieldValue(
  jobId: string, customFieldId: string, keyedOnLabel: string,
): Promise<string | null> {
  const { data } = await apiClient.get<{ value: string | null }>(
    `/jobs/${jobId}/custom-fields/${customFieldId}/suggested-value`,
    { params: { keyed_on_label: keyedOnLabel } },
  );
  return data.value;
}

export interface CustomFieldReferenceSearchResult {
  value: string;
  /** Ready to show as-is, e.g. "Shanghai — China (CNSHA)". */
  label: string;
}

/** The manual fallback for a reference-backed field too large to ever offer as a plain
 * dropdown (e.g. Shipment Port Code's ~72,000-row world port table) - searches it server-side
 * by a substring of the country or place name, for when the automatic lookup finds nothing and
 * the operator has to pick the right row themselves by inspecting the document. */
export async function searchCustomFieldReferenceValues(
  jobId: string, customFieldId: string, query: string,
): Promise<CustomFieldReferenceSearchResult[]> {
  const { data } = await apiClient.get<{ results: CustomFieldReferenceSearchResult[] }>(
    `/jobs/${jobId}/custom-fields/${customFieldId}/reference-search`,
    { params: { q: query } },
  );
  return data.results;
}

/** One piece of a composite field's value: either another field's own label_name (a plain
 *  string) or a fixed literal value typed straight in - the SAME text on every product line
 *  rather than something read off the job (e.g. a separator, a fixed prefix). */
export type CompositePiece = string | { fixed: string };

export interface CompositeFieldConfig {
  label_name: string;
  pieces: CompositePiece[];
  /** Aligned 1:1 with pieces - the separator text inserted before that piece. "" = none. */
  separators: string[];
  available_labels: string[];
  /** A handful of real product lines already seen for this consignee - every per-row field
   *  label this template has, for the Combined Description Configuration screen's own live
   *  preview and "Preview by Part Code" sample table. */
  sample_rows: Record<string, string>[];
}

/** Every consignee the Combined Description Configuration screen's own picker can offer -
 *  every distinct consignee name a real job of this template has ever had. */
export async function getKnownConsignees(groupId: string): Promise<string[]> {
  const { data } = await apiClient.get<string[]>(`/template-groups/${groupId}/known-consignees`);
  return data;
}

/** The Combined Description Configuration screen's own data for one consignee - their
 *  already-saved format if they have one, else the template's generic fallback. */
export async function getCompositeFieldConfig(
  groupId: string,
  consignee: string,
  labelName = "Combined Description",
): Promise<CompositeFieldConfig> {
  const { data } = await apiClient.get<CompositeFieldConfig>(
    `/template-groups/${groupId}/composite-field-config`,
    { params: { consignee, label_name: labelName } },
  );
  return data;
}

export interface CompositeFieldConfigSaveResult {
  /** Set only when jobId was passed - that job's own recomputed field values, so the change
   *  is visible right away without a second request. */
  field_values: JobFieldValue[] | null;
}

/** Saves a Combined Description format directly against a consignee - independent of any one
 *  job. The latest save always replaces what this consignee already had, no confirmation step
 *  (a deliberate admin action, not an incidental edit made while working a job). Pass jobId
 *  when saved from inside an open job, to recompute and return that job's own field values
 *  immediately. */
export async function saveCompositeFieldConfig(
  groupId: string,
  payload: {
    consignee: string;
    labelName?: string;
    pieces: CompositePiece[];
    separators: string[];
    jobId?: string;
  },
): Promise<CompositeFieldConfigSaveResult> {
  const { data } = await apiClient.put<CompositeFieldConfigSaveResult>(
    `/template-groups/${groupId}/composite-field-config`,
    {
      consignee: payload.consignee,
      label_name: payload.labelName ?? "Combined Description",
      pieces: payload.pieces,
      separators: payload.separators,
      job_id: payload.jobId ?? null,
    },
  );
  return data;
}

/** Sets (or clears, with null) a job's GK1-set target date — drives the ETA boxes on the
 *  operator dashboard. */
export async function setJobEta(jobId: string, etaDate: string | null): Promise<JobDetail> {
  const { data } = await apiClient.patch<JobDetail>(`/jobs/${jobId}/eta`, { eta_date: etaDate });
  return data;
}

/** Sets (or clears, with null) which operator a job is assigned to — the dropdown on the
 *  Jobs list. Always editable: reassigning just overwrites the previous value, no locking. */
export async function assignJobOperator(jobId: string, operatorId: string | null): Promise<JobDetail> {
  const { data } = await apiClient.patch<JobDetail>(`/jobs/${jobId}/assign`, { operator_id: operatorId });
  return data;
}

/** Direct URL for a document the ERP produced during entry. Served by the API with an
 *  allow-list check against what the run actually recorded, so the name cannot escape the job. */
export function capturedFileUrl(jobId: string, fileName: string): string {
  return `${apiClient.defaults.baseURL}/jobs/${jobId}/captured/${encodeURIComponent(fileName)}`;
}

/** What the dump will contain, laid out before anything is submitted.
 *
 *  Computed by the API from the SAME values and sheet plans the real run uses — a preview
 *  worked out a second way would drift from the file it claims to describe. */
export interface DumpSheet {
  sheet: string;
  scope: string;
  columns: {
    column: string | null;
    header: string | null;
    field: string;
    literal?: boolean;
    /** Where the value comes from: a document, the customer's reference sheet, an AI
     *  computation, a fixed value, or nothing at all (an unmapped template column). */
    source?: "document" | "reference" | "computed" | "fixed" | "none";
  }[];
  rows: string[][];
  row_count: number;
}
export interface DumpPreview {
  mode: string;
  sheets: DumpSheet[];
  fields: { field: string; value: string }[];
  line_count: number;
  summary: string;
}

export async function dumpPreview(jobId: string): Promise<DumpPreview> {
  const { data } = await apiClient.get<DumpPreview>(`/jobs/${jobId}/dump-preview`);
  return data;
}

/** Record that the operator finished a stage and moved on.
 *
 *  The history is otherwise built from status changes alone, and there are only five of
 *  those — so it could never show the seven stages someone actually works through. */
export async function recordStage(jobId: string, stage: string): Promise<void> {
  await apiClient.post(`/jobs/${jobId}/stage`, { stage });
}

/** Operator presses "Approved and Proceed" on ONE document, on Data Extraction. */
export async function approveDocument(
  jobId: string,
  jobDocumentId: string,
  approved = true,
): Promise<JobDetail> {
  const { data } = await apiClient.post<JobDetail>(
    `/jobs/${jobId}/documents/${jobDocumentId}/approve`,
    { approved },
  );
  return data;
}

/** Operator presses "Approved and Proceed" on Data Validation. Refused (400) while a
 *  cross-check is still open — the screen should not offer this until blockedBecause
 *  agrees there is nothing left to decide. */
export async function approveValidation(jobId: string): Promise<JobDetail> {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/validation/approve`);
  return data;
}

/** GK1 (operator) presses "Final Submit for Approval" on ERP Submission — hands the job to
 *  a GK2 user instead of running the real ERP entry (not wired up yet). Refused (409) unless
 *  Data Validation is already approved. */
export async function submitForGk2Approval(jobId: string): Promise<JobDetail> {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/gk2/submit-for-approval`);
  return data;
}

/** GK2 presses "Final Approve & Proceed" — moves the job to "AI - Preparing for ERP"
 *  immediately, then a background thread on the server builds the import workbook, replays
 *  the tenant's ready ERP script against the real ERP ("ERP Entry Process Started"), and
 *  lands on "AI - ERP Submitted" or "Failed" (see gk2_status). See gk2ApproveAndProceed in
 *  JobRunPage.tsx for the polling that watches it land. */
export async function gk2Approve(jobId: string): Promise<JobDetail> {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/gk2/approve`);
  return data;
}

/** A human confirming the AI/ERP automation actually submitted this job - the Submitted
 *  Jobs screen's "Verified" button. Moves the job on to Completed Jobs (see Job.category);
 *  refused (409) for a job that hasn't reached "AI - ERP Submitted" yet. */
export async function verifyJob(jobId: string): Promise<JobDetail> {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/verify`);
  return data;
}

/** Operator decided a job flagged "possible_duplicate" is genuinely a separate shipment -
 *  let it proceed as if never flagged. "Delete" uses deleteJob() instead - there is nothing
 *  duplicate-specific about removing a job. "Hold" needs no call at all: leave the popup and
 *  the job stays possible_duplicate until someone comes back to it. */
export async function approveDuplicate(jobId: string): Promise<JobDetail> {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/duplicate-decision`, {
    decision: "approve",
  });
  return data;
}
