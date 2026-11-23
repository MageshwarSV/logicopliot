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

export interface DateRange {
  date_from?: string;
  date_to?: string;
}

export interface SummaryFilters extends DateRange {
  min_jobs: number;
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

export async function getEdits(field: CorrectedField, range: DateRange, tenant_id?: string): Promise<CorrectionEdits> {
  const { data } = await apiClient.get<CorrectionEdits>("/extraction-corrections/edits", {
    params: clean({
      group_id: field.group_id,
      label_name: field.label_name,
      template_document_id: field.template_document_id ?? undefined,
      ...range,
      tenant_id,
    }),
  });
  return data;
}

// ---------------------------------------------------------------- Fix (prompt editor)

export interface PromptHistoryItem {
  id: string;
  at: string;
  by: string | null;
  action: "apply" | "revert";
  old_prompt: string | null;
  new_prompt: string | null;
  old_format_hint: string | null;
  new_format_hint: string | null;
}

export interface FixInfo {
  kind: "mark" | "custom";
  field_id: string;
  label_name: string;
  editable: boolean;
  reason: string | null;
  prompt: string;
  format_hint: string | null;
  has_format_hint: boolean;
  format_rule: string | null;
  multi_value: boolean;
  model: string;
  reads_images: boolean;
  test_supported: boolean;
  history: PromptHistoryItem[];
}

export interface FixSuggestion {
  analysis: string;
  prompt: string;
  format_hint: string | null;
  model: string;
  corrections_used: number;
  images_used: number;
}

export interface FixTestLine {
  job_id: string;
  job_reference: string;
  row_index: number | null;
  ai_value: string | null;
  new_value: string | null;
  corrected_value: string;
  error: string | null;
  old_matches: boolean;
  new_matches: boolean;
}

export interface FixTestResult {
  lines: FixTestLine[];
  old_correct: number;
  new_correct: number;
  total: number;
}

function ref(field: CorrectedField) {
  return {
    group_id: field.group_id,
    template_document_id: field.template_document_id,
    label_name: field.label_name,
  };
}

export async function getFix(field: CorrectedField): Promise<FixInfo> {
  const { data } = await apiClient.get<FixInfo>("/extraction-corrections/fix", {
    params: clean({ ...ref(field), template_document_id: field.template_document_id ?? undefined }),
  });
  return data;
}

export async function suggestFix(field: CorrectedField): Promise<FixSuggestion> {
  const { data } = await apiClient.post<FixSuggestion>("/extraction-corrections/fix/suggest", ref(field));
  return data;
}

export async function testFix(field: CorrectedField, prompt: string, format_hint: string | null): Promise<FixTestResult> {
  const { data } = await apiClient.post<FixTestResult>("/extraction-corrections/fix/test", { ...ref(field), prompt, format_hint });
  return data;
}

export async function applyFix(field: CorrectedField, prompt: string, format_hint: string | null): Promise<FixInfo> {
  const { data } = await apiClient.post<FixInfo>("/extraction-corrections/fix/apply", { ...ref(field), prompt, format_hint });
  return data;
}

export async function revertFix(field: CorrectedField, change_id: string): Promise<FixInfo> {
  const { data } = await apiClient.post<FixInfo>("/extraction-corrections/fix/revert", { ...ref(field), change_id });
  return data;
}
