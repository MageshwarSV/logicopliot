// Shapes returned by /api/v1/support/app/* (backend app/api/v1/support_app.py).

export type HealthStatus = "ok" | "warning" | "down" | "unknown";

export type Activity = {
  id: string;
  kind: string;
  text: string;
  at: string;
  by: string | null;
  client: string | null;
  job_id: string | null;
  reference: string | null;
  outcome?: string | null;
  outcome_label?: string | null;
};

export type Home = {
  health: {
    status: HealthStatus;
    problems: { key: string; label: string; status: HealthStatus; detail: string }[];
    down: number;
    checked_at: string;
  };
  today: { total_jobs: number; submitted: number; failed: number; delayed: number; pages: number };
  stuck_in_ai: number;
  open_problems: number;
  open_by_kind: Record<string, number>;
  alerts: Activity[];
};

export type LastNote = {
  kind: string;
  outcome: string | null;
  outcome_label: string | null;
  by: string | null;
  at: string;
  text: string;
} | null;

export type Issue = {
  job_id: string;
  reference: string;
  client: string;
  type: string;
  template: string;
  kind: string;
  stage: string;
  reason: string;
  since: string | null;
  age_minutes: number | null;
  first_erp_at: string | null;
  since_first_erp_minutes: number | null;
  can_retry_erp: boolean;
  can_retry_extraction: boolean;
  handled: boolean;
  handled_note: string | null;
  issue_key: string;
  issue_title: string;
  known_fix: string | null;
  last_note: LastNote;
};

export type JobDetail = {
  job_id: string;
  reference: string;
  client: string;
  type: string;
  template: string;
  stage: string | null;
  problem: string | null;
  reason: string | null;
  since: string | null;
  age_minutes: number | null;
  handled: boolean;
  can_retry_erp: boolean;
  can_retry_extraction: boolean;
  erp_message: string | null;
  corrections: { field: string; needs: string; example: string; suggest: string; value: string }[];
  issue_key: string | null;
  issue_title: string | null;
  known_fix: string | null;
  timeline: { label: string; at: string }[];
  events: { at: string; status: string; note: string | null }[];
  notes: Activity[];
  erp_failures: { at: string; reason: string; status: string }[];
};

export type KnownGroup = {
  key: string;
  title: string;
  fix: string | null;
  count: number;
  jobs: { job_id: string; reference: string; client: string }[];
};

export const OUTCOMES: { key: string; label: string }[] = [
  { key: "investigating", label: "Investigating" },
  { key: "fixed", label: "Fixed" },
  { key: "customer_informed", label: "Customer informed" },
  { key: "erp_side", label: "ERP side issue" },
  { key: "not_our_issue", label: "Not our issue" },
];
