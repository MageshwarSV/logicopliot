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
  };
  pipeline: { key: string; label: string; count: number }[];
  volume: { day: string; jobs: number; pages: number }[];
  attention: { key: string; label: string; count: number }[];
}

export type IssueTab = "all" | "extraction" | "erp" | "other";

export interface SupportIssue {
  job_id: string;
  reference: string;
  client: string;
  type: string;
  template: string;
  kind: "erp_failed" | "extraction_failed" | "extraction_slow" | "missing_documents";
  tab: Exclude<IssueTab, "all">;
  stage: string;
  pages: number;
  reason: string;
  since: string | null;
  age_minutes: number | null;
  can_retry_erp: boolean;
  can_retry_extraction: boolean;
}

export interface SupportIssues {
  counts: Record<IssueTab, number>;
  items: SupportIssue[];
}

export async function getOverview(date_from: string, date_to: string): Promise<SupportOverview> {
  const { data } = await apiClient.get<SupportOverview>("/support/overview", { params: { date_from, date_to } });
  return data;
}

export async function getIssues(tab: IssueTab = "all"): Promise<SupportIssues> {
  const { data } = await apiClient.get<SupportIssues>("/support/issues", { params: { tab } });
  return data;
}

/** Retry = the job's own existing endpoints; the server allows Support only on a failed job. */
export async function retryErp(jobId: string): Promise<void> {
  await apiClient.post(`/jobs/${jobId}/gk2/approve`);
}

export async function retryExtraction(jobId: string): Promise<void> {
  await apiClient.post(`/jobs/${jobId}/extract`);
}
