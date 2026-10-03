export interface Job {
  id: string;
  tenant_id: string;
  group_id: string;
  reference: string;
  status: string;
  stage?: string | null; // Documents | Verification | ERP Entry | Completed
  /** The single word for the job list and header — coarser than `stage` and the one to
   *  actually show: AI Processing, Ready for Review, Review in Progress, Ready for
   *  Submission, Submitted, Completed, Failed, Duplicate, Document Capture. */
  outer_status?: string | null;
  /** The consignee on this job's own documents - NOT the template's name. */
  customer_name?: string | null;
  /** "Sea Import", "Air Export" — worked out from the job's own values. Empty when the
   *  documents do not say. */
  mode?: string | null;
  assigned_operator_id?: string | null;
  operator_name?: string | null;
  /** Who actually emailed this job in, while it is still genuinely unassigned - an
   *  informational hint for the Assigned To column, never a real assignment. Null the
   *  moment a real operator is assigned. */
  pulled_from_sender?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  /** Set when status is "possible_duplicate" - the job this one's extracted data matched.
   *  duplicate_of_reference is the Job No to show in the popup; the id alone means nothing
   *  to an operator. */
  duplicate_of_job_id?: string | null;
  duplicate_of_reference?: string | null;
  /** A GK1-set target date ("YYYY-MM-DD"), edited from the Jobs list. null = not set. */
  eta_date?: string | null;
  /** See JobDetail - surfaced on the list too so a job silently missing its extracted data
   *  doesn't only show up once someone happens to open it. */
  needs_reextraction?: boolean;
}

/** One line of a job's history - what the Last updated popup lists. */
export interface JobEvent {
  id: string;
  status: string;
  /** What to CALL this status, sent by the server. The screen used to keep its own copy of
   *  these words and the two had drifted apart. Optional only for rows saved before it. */
  label?: string | null;
  stage?: string | null;
  note?: string | null;
  at: string;
}

export interface JobDocument {
  id: string;
  template_document_id: string;
  name: string;
  doc_type: string;
  is_uploaded: boolean;
  page_count: number;
  /** A slot can hold several files — three invoices for one shipment. Position within it. */
  file_index: number;
  /**
   * Which invoice this file was paired into, matched on the invoice numbers the documents
   * carry. null means it covers the whole job (one bill of lading for all three invoices).
   */
  set_index: number | null;
  original_name: string | null;
  /** Operator (GK1) pressed "Submit for Approval" on this document, on Data Extraction. */
  approved?: boolean;
  /** GK2's OWN independent approval of this document — separate from GK1's `approved`. */
  gk2_approved?: boolean;
  /** False = optional — Document Capture can complete without this document uploaded. */
  is_required?: boolean;
  /** Bumped whenever this slot's file is replaced - a same-slot delete+reupload reuses the
   *  SAME id and often the same page_count, so this is what tells the preview the file
   *  actually changed and its cached page images must be re-fetched, not reused. */
  updated_at: string;
}

export interface JobFieldValue {
  id: string;
  /** null for a value read straight off a document (identified by mark_id instead) - only a
   *  custom/computed field carries this. Used to find a PAIRED field's own value by id. */
  custom_field_id?: string | null;
  mark_id: string;
  template_document_id: string;
  document_name: string;
  label_name: string;
  extracted_value: string | null;
  corrected_value: string | null;
  value: string | null;
  /** Answers itself from the customer's reference sheet (a lookup); its own value counts
   *  as confirmed. Everything else needs the operator to confirm it, pre-filled or not. */
  self_filled?: boolean;
  /** Super Admin ticked this field as needing operator confirmation before ERP entry. */
  ask_operator?: boolean;
  /** false = optional: asked for, but never blocks Submit Entry. */
  ask_operator_required?: boolean;
  /** What the Super Admin told the operator to enter. */
  ask_operator_hint?: string | null;
  /** Which uploaded FILE this value was read from, and the invoice-set that file was
   *  paired into. A slot can hold several files, so the document name alone does not
   *  say which of three invoices a value came off. */
  job_document_id?: string | null;
  set_index?: number | null;
  /** For a COMPUTED field: the documents it reads. A computed value has no document of
   *  its own, so this is how it gets shown against the ones it came from. */
  source_document_ids?: string[];
  /** What produced the value: document | reference | computed | fixed. */
  origin?: "document" | "reference" | "computed" | "fixed";
  /** 1-based line-item row, when this field repeats. */
  row_index?: number | null;
  /** FieldMark's static, one-time-drawn template position — NOT used for the highlight
   *  anymore (every real document has its own layout, so a fixed template box is only right
   *  by coincidence). Kept for reference only; see found_* below for the real thing. */
  mark_page?: number | null;
  mark_x?: number | null;
  mark_y?: number | null;
  mark_width?: number | null;
  mark_height?: number | null;
  /** Where this value's own text was actually found on THIS real document (normalized 0-1,
   *  an OCR word-box match) — what the Data Extraction screen highlights/scrolls to when
   *  this field is focused. null when extraction used the vision fallback (no OCR ran) or
   *  found no confident text match — show no highlight rather than guess. */
  found_page?: number | null;
  found_x?: number | null;
  found_y?: number | null;
  found_width?: number | null;
  found_height?: number | null;
  /** This custom field is paired with another one - the operator picks which one is right
   *  per row, and the pick writes into THIS field's own corrected value. See the backend's
   *  CustomField.paired_custom_field_id for the full picture. null = not paired. */
  paired_custom_field_id?: string | null;
  /** Heading shown above the two options, e.g. "CTH - pick which one is right for this
   *  line". Falls back to a generic heading when null/blank. */
  picker_heading?: string | null;
  /** Other custom_field ids that also get set to whichever value is picked, on the same
   *  row (e.g. RITC following the CTH pick). */
  sync_field_ids?: string[] | null;
}

export type VerificationStatus = "match" | "mismatch" | "missing" | "review";

export interface VerificationRow {
  link_id: string;
  field_label: string;
  source_document: string;
  source_value: string | null;
  target_document: string;
  target_value: string | null;
  status: VerificationStatus;
  accepted: boolean;
}

export interface JobDetail {
  id: string;
  tenant_id: string;
  group_id: string;
  group_name: string;
  reference: string;
  status: string;
  /** "Sea Import", "Air Export" — worked out from the job's own values. */
  mode?: string | null;
  stage?: string | null;
  outer_status?: string | null;
  assigned_operator_id?: string | null;
  operator_name?: string | null;
  /** Whether this job's template submits via a bulk Excel import (TemplateGroup.entry_mode)
   *  rather than typed-field browser automation - offer "Download as Excel" only here. */
  excel_entry?: boolean;
  /** The raw GK2 sign-off state: null | "pending" | "preparing_erp" | "entering_erp" |
   *  "submitted" | "failed". Use this, not outer_status, to tell "GK2 has this job" apart
   *  from an ordinary GK1 entry that also happens to read "Failed". */
  gk2_status?: string | null;
  /** Operator (GK1) pressed "Approved and Proceed" on Data Validation, for the current
   *  extraction. */
  validation_approved?: boolean;
  /** GK2's OWN independent approval of Data Validation — separate from GK1's
   *  `validation_approved`. */
  gk2_validation_approved?: boolean;
  /** GK1's IRN Documents Upload stage - set once an operator explicitly presses Approval for
   *  IRN or Skip. Persisted, unlike the old client-only "irnApproved" state. */
  irn_documents_done?: boolean;
  /** Which of the two IRN Documents Upload actions GK1 pressed - True for Approval for IRN,
   *  False for Skip (and for every job finished before this field existed). Drives whether
   *  the per-document DSC + IRN Number placeholder shows, and whether GK2's Final Approve &
   *  Proceed runs the real ERP submission or parks the job in "IRN Document Process". */
  irn_approval_requested?: boolean;
  /** A document was removed after this job was already extracted (an operator deleting the
   *  wrong file, or the custom-filter-page sweep stripping one that turned out to be junk-
   *  reference content) - its stale custom fields were cleared, and this asks for a fresh
   *  Extract. Cleared automatically the next time extraction runs. */
  needs_reextraction?: boolean;
  /** See Job - same meaning, for this job's own duplicate popup. */
  duplicate_of_job_id?: string | null;
  duplicate_of_reference?: string | null;
  eta_date?: string | null;
  documents: JobDocument[];
  field_values: JobFieldValue[];
  verifications: VerificationRow[];
  all_checks_passed: boolean;
  erp_status?: string | null; // ok | partial | error | no_script | failed
  erp_final_url?: string | null;
  erp_log?: string[] | null;
  erp_screenshot?: string | null;
  erp_reason?: string | null;
  erp_failed_field?: string | null; // data field (label_name) the ERP rejected
  erp_failed_value?: string | null;
  /** Values, text and documents the ERP entry read back OUT of the ERP, keyed by the label
   *  the Super Admin gave them. Older runs stored a bare string. */
  erp_captured?: Record<string, CapturedItem | string> | null;
  /** Plain-words description of the screen the run stopped on, read by AI from a screenshot. */
  erp_diagnosis?: string | null; // the value that wasn't found in the ERP
}

/** One thing picked out of the ERP during entry, shown on the operator's completed screen. */
export interface CapturedItem {
  label: string;
  value: string;
  kind: "value" | "text" | "document" | string;
  description?: string;
  file?: string | null;
  /** internal = reused in a later step only; output/both = shown to the operator. */
  usage?: "internal" | "output" | "both";
}
