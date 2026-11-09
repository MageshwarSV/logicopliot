/** Pure display-layer relabeling of a job's outer_status/stage text, per the "CARGORA - Status
 *  Mapping (Updated)" scheme. Deliberately NOT a change to the underlying strings themselves -
 *  every comparison/filter/color-class check elsewhere (JobsOverview.tsx's dashboard box
 *  counts, JobsPage.tsx/JobRunPage.tsx's own busy/color logic) keys off the ORIGINAL backend
 *  value and must keep doing so; this map only swaps the final text shown to the user, at the
 *  one spot each page actually renders it. */
const STATUS_DISPLAY_MAP: Record<string, string> = {
  "Document Capture": "Pre-Alert Received",
  "Missing Documents": "Missing Documents (Exception)",
  "AI - Processing": "AI Processing",
  "GK1 Review": "Ready for Review",
  "GK1 Reviewing": "In Review",
  "Ready for Submission": "In Review",
  "Pending GK2 Approval": "In Review",
  "IRN Document Process": "In Review",
  "AI - Preparing for ERP": "ERP Submission Started",
  "ERP Entry Process Started": "ERP Submission Started",
  "Submitted": "ERP Submitted",
  "AI - ERP Submitted": "ERP Submitted",
  "Completed": "ERP Submitted",
  "Failed": "Failed (Exception)",
  "Possible Duplicate": "Possible Duplicate (Exception)",
};

export function displayStatusLabel(raw: string | null | undefined): string {
  if (!raw) return "";
  return STATUS_DISPLAY_MAP[raw] ?? raw;
}
