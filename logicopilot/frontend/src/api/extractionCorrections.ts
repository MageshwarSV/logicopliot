import apiClient from "./client";

export interface CorrectionExample {
  job_reference: string;
  ai_value: string | null;
  final_value: string | null;
}

export interface CorrectedField {
  group_id: string;
  template_document_id: string | null;
  label_name: string;
  template: string;
  document: string;
  tenant: string;
  jobs_corrected: number;
  total_edits: number;
  lines_corrected: number;
  jobs_with_field: number;
  correction_rate: number | null;
  wrong: number;
  missed: number;
  cleared: number;
  last_seen: string | null;
  examples: CorrectionExample[];
}

export interface CorrectionSummary {
  fields: CorrectedField[];
  history_since: string | null;
  min_jobs: number;
}

export interface EditHistoryItem {
  before: string | null;
  after: string | null;
  by: string | null;
  at: string;
  source: string;
}

export interface CorrectedLine {
  job_id: string;
  job_reference: string;
  job_document_id: string | null;
  row_index: number | null;
  set_index: number | null;
  ai_value: string | null;
  final_value: string | null;
  edit_count: number | null;
  kind: "wrong" | "missed" | "cleared";
  job_exists: boolean;
  last_at: string | null;
  history: EditHistoryItem[];
}

export interface CorrectionEdits {
  lines: CorrectedLine[];
  total_lines: number;
  jobs: number;
}

export interface SummaryFilters {
  min_jobs: number;
  days?: number;
  tenant_id?: string;
  group_id?: string;
  include_operator_fields?: boolean;
}

function clean<T extends object>(o: T): Partial<T> {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined && v !== "")) as Partial<T>;
}

export async function getSummary(filters: SummaryFilters): Promise<CorrectionSummary> {
  const { data } = await apiClient.get<CorrectionSummary>("/extraction-corrections/summary", {
    params: clean(filters),
  });
  return data;
}

export async function getEdits(field: CorrectedField, days?: number, tenant_id?: string): Promise<CorrectionEdits> {
  const { data } = await apiClient.get<CorrectionEdits>("/extraction-corrections/edits", {
    params: clean({
      group_id: field.group_id,
      label_name: field.label_name,
      template_document_id: field.template_document_id ?? undefined,
      days,
      tenant_id,
    }),
  });
  return data;
}
