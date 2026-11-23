import apiClient from "./client";

export interface CustomerPages {
  tenant_id: string;
  tenant_name: string | null;
  pages_billed: number;
  original_pages: number;
  rerun_pages: number;
  classified_pages?: number;
  unclassified_pages?: number;
  total_jobs: number;
}

export interface CostRow {
  usage_date: string;
  project_id: string;
  project_name: string | null;
  cost_usd: number;
  cost_inr: number | null;
  usd_inr_rate: number | null;
  rate_source: string | null;
  input_tokens: number;
  output_tokens: number;
  requests: number;
  pages_billed: number;
  original_pages: number;
  rerun_pages: number;
  classified_pages: number;
  unclassified_pages: number;
  total_jobs: number;
  cost_per_page_usd: number | null;
  cost_per_page_inr: number | null;
  status: "provisional" | "final" | "failed";
  fetched_at: string | null;
  reconciled_at: string | null;
  customers?: CustomerPages[];
}

export interface CostTotals {
  cost_usd: number;
  cost_inr: number;
  pages_billed: number;
  original_pages: number;
  rerun_pages: number;
  classified_pages: number;
  unclassified_pages: number;
  total_jobs: number;
  input_tokens: number;
  output_tokens: number;
  requests: number;
  cost_per_page_usd: number | null;
  cost_per_page_inr: number | null;
  provisional_days: number;
  failed_days: number;
}

export interface MonthRow extends CostTotals {
  month: string;
  days_recorded: number;
  customers: CustomerPages[];
}

export interface DayDetail {
  day: string;
  projects: CostRow[];
  runs: { kind: string; status: string; error: string | null; triggered_by: string | null; started_at: string; finished_at: string | null }[];
}

export async function getDaily(month: string) {
  const { data } = await apiClient.get<{ month: string; days: CostRow[]; totals: CostTotals }>("/api-cost/daily", { params: { month } });
  return data;
}

export async function getMonthly() {
  const { data } = await apiClient.get<{ months: MonthRow[] }>("/api-cost/monthly");
  return data;
}

export async function getDay(day: string) {
  const { data } = await apiClient.get<DayDetail>(`/api-cost/day/${day}`);
  return data;
}

export async function runDays(date_from: string, date_to?: string) {
  const { data } = await apiClient.post<{ started: boolean; days: number }>("/api-cost/run", { date_from, date_to: date_to || null });
  return data;
}
