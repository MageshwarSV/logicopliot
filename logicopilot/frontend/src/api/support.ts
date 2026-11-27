import { apiClient } from "./client";

/** Production Support (backend: api/v1/support.py). */
export interface SupportOverview {
  date_from: string;
  date_to: string;
  tiles: {
    total_jobs: number;
    pages: number;
    avg_pages: number | null;
    attachments: number;
    submitted: number;
    submitted_pct: number | null;
    failed: number;
    failed_pct: number | null;
    delayed: number;
    delayed_pct: number | null;
  };
  pipeline: { key: string; label: string; count: number }[];
  volume: { day: string; jobs: number; pages: number }[];
  attention: { key: string; label: string; count: number }[];
  attention_window_days: number;
  activity: SupportActivity[];
}

export type IssueTab = "all" | "extraction" | "erp" | "other";

export interface SupportIssue {
  job_id: string;
  reference: string;
  client: string;
  type: string;
  template: string;
  kind: "erp_failed" | "erp_slow" | "extraction_failed" | "extraction_slow" | "missing_documents";
  tenant_id: string;
  tab: Exclude<IssueTab, "all">;
  stage: string;
  pages: number;
  reason: string;
  since: string | null;
  age_minutes: number | null;
  can_retry_erp: boolean;
  can_retry_extraction: boolean;
  handled: boolean;
  handled_note: string | null;
  created_at: string | null;
  /** First time it was sent to the ERP - retries keep it. */
  first_erp_at: string | null;
  last_erp_at: string | null;
  since_first_erp_minutes: number | null;
}

export interface SupportIssues {
  counts: Record<IssueTab, number>;
  items: SupportIssue[];
}

export async function getOverview(date_from: string, date_to: string): Promise<SupportOverview> {
  const { data } = await apiClient.get<SupportOverview>("/support/overview", { params: { date_from, date_to } });
  return data;
}

export async function getIssues(tab: IssueTab = "all", includeHandled = false): Promise<SupportIssues> {
  const { data } = await apiClient.get<SupportIssues>("/support/issues", { params: { tab, include_handled: includeHandled } });
  return data;
}

export async function markHandled(jobId: string, text: string): Promise<void> {
  await apiClient.post(`/support/issues/${jobId}/handled`, { text });
}

export interface SupportActivity {
  id: string;
  kind: "note" | "customer_call" | "handled" | "retry_erp" | "retry_extraction" | "alert";
  text: string;
  at: string;
  by: string | null;
  client: string | null;
  job_id: string | null;
  reference: string | null;
}

export async function getActivity(params: { limit?: number; kind?: string; job_id?: string } = {}): Promise<SupportActivity[]> {
  const { data } = await apiClient.get<{ items: SupportActivity[] }>("/support/activity", { params });
  return data.items;
}

export async function addActivity(body: { kind: "note" | "customer_call"; text: string; tenant_id?: string | null; job_id?: string | null }): Promise<SupportActivity> {
  const { data } = await apiClient.post<SupportActivity>("/support/activity", body);
  return data;
}

export interface HealthBox { key: string; label: string; status: "ok" | "warn" | "down" | "unknown"; detail: string }
export interface SupportHealth {
  status: HealthBox["status"];
  components: HealthBox[];
  queues: { name: string; label: string; waiting: number; running: number; oldest_wait_seconds: number | null; stale: boolean }[];
  servers: { host: string; role: string; seen_seconds_ago: number; mail_workers?: number; extract_workers?: number; erp_workers?: number }[];
  checked_at: string;
}

export async function getHealth(): Promise<SupportHealth> {
  const { data } = await apiClient.get<SupportHealth>("/support/health");
  return data;
}

export interface StagePerf { key: string; label: string; sla: number; measured: number; avg: number | null; p90: number | null; over_sla: number; ok: boolean }
export interface SupportPerformance {
  stages: StagePerf[];
  delayed: { job_id: string; reference: string; client: string; created_at: string; stages: { stage: string; minutes: number; sla: number }[] }[];
  delayed_count: number;
  jobs: number;
  note: string;
}

export async function getPerformance(date_from: string, date_to: string): Promise<SupportPerformance> {
  const { data } = await apiClient.get<SupportPerformance>("/support/performance", { params: { date_from, date_to } });
  return data;
}

export interface MonitoredJob {
  job_id: string; reference: string; client: string; type: string; template: string;
  stage: string; stage_label: string; pages: number; created_at: string;
  timings: Record<"email_to_ai" | "queue_wait" | "extraction" | "erp", number | null>;
  delayed: boolean;
}
export interface SupportJobs {
  total: number;
  items: MonitoredJob[];
  stages: { key: string; label: string }[];
  customers: { id: string; name: string }[];
}

export async function getJobs(params: { date_from: string; date_to: string; tenant_id?: string; stage?: string; q?: string; limit?: number }): Promise<SupportJobs> {
  const { data } = await apiClient.get<SupportJobs>("/support/jobs", { params });
  return data;
}

export interface CustomerRow { tenant_id: string; name: string; jobs: number; submitted: number; failed: number; in_progress: number; last_job_at: string | null; open_issues: number }

export async function getCustomers(date_from: string, date_to: string): Promise<CustomerRow[]> {
  const { data } = await apiClient.get<{ items: CustomerRow[] }>("/support/customers", { params: { date_from, date_to } });
  return data.items;
}

export interface SupportSettings {
  sla: Record<string, number>;
  alert_emails: string[];
  defaults: Record<string, number>;
  labels: Record<string, string>;
  email_configured: boolean;
}

export async function getSettings(): Promise<SupportSettings> {
  const { data } = await apiClient.get<SupportSettings>("/support/settings");
  return data;
}

export async function saveSettings(body: { sla?: Record<string, number>; alert_emails?: string[] }): Promise<SupportSettings> {
  const { data } = await apiClient.put<SupportSettings>("/support/settings", body);
  return data;
}

export async function testAlert(): Promise<{ sent_to: string[] }> {
  const { data } = await apiClient.post<{ sent_to: string[] }>("/support/alerts/test");
  return data;
}

/** Retry = the job's own existing endpoints; the server allows Support only on a failed job. */
export async function retryErp(jobId: string): Promise<void> {
  await apiClient.post(`/jobs/${jobId}/gk2/approve`);
}

export async function retryExtraction(jobId: string): Promise<void> {
  await apiClient.post(`/jobs/${jobId}/extract`);
}
